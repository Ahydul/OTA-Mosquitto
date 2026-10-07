import hashlib
import json
import os
import argparse
import base64
import time
import subprocess

def calculate_sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()

def process_firmware(filepath: str, version: str):
    if not os.path.exists(filepath):
        print(f"Error: {filepath} not found.")
        return
        
    file_size = os.path.getsize(filepath)
    if file_size < 4:
        raise ValueError("File too small")

    with open(filepath, 'rb') as f:
        data = f.read()

    base_size = file_size // 4
    remainder = file_size % 4

    chunks = []
    chunk_metadata = []
    start_idx = 0
    
    for i in range(4):
        chunk_size = base_size + (1 if i < remainder else 0)
        chunk_data = data[start_idx:start_idx + chunk_size]
        chunks.append(chunk_data)
        
        filename = f"chunk_{i}.bin"
        with open(filename, 'wb') as chunk_file:
            chunk_file.write(chunk_data)
            
        chunk_metadata.append({"index": i, "filename": filename})
        start_idx += chunk_size

    leaf_hashes = [calculate_sha256(chunk) for chunk in chunks]

    h_01 = calculate_sha256(leaf_hashes[0] + leaf_hashes[1])
    h_23 = calculate_sha256(leaf_hashes[2] + leaf_hashes[3])
    
    merkle_root = calculate_sha256(h_01 + h_23).hex()

    manifest = {
        "firmware_version": version,
        "original_firmware_size_bytes": file_size,
        "number_chunks": len(chunks),
        "merkle_root": merkle_root,
        "chunks": chunk_metadata
    }

    with open("manifest.json", "w") as manifest_file:
        json.dump(manifest, manifest_file, indent=4)

    print("Firmware processed successfully. Manifest and chunks generated.")
    return manifest

def publish_update(manifest: dict, args:dict, manifest_topic: str):
    broker_host = args.host
    broker_port = args.port
    username = args.username
    password = args.password
    simulate_duplicates = args.simulate_duplicates
    simulate_manifest_delay = args.simulate_manifest_delay


    def _publish(data, topic):
        subprocess.run([
            "mosquitto_pub", 
            "-h", broker_host, 
            "-p", str(broker_port), 
            "-t", topic, 
            "-m", json.dumps(data), 
            "-u", "pepito", 
            "-P", "pepito2",
            "-q", "1"
        ])

    if not simulate_manifest_delay:
        print("Publishing manifest json...")
        _publish(manifest_data, manifest_topic)

    for chunk_info in manifest["chunks"]:
        idx = chunk_info["index"]
        filename = chunk_info["filename"]
        if args.simulate_missing_chunk and idx==0:
            print("Skipping first chunk")
            continue
        time.sleep(0.05)

        
        with open(filename, 'rb') as f:
            raw_data = f.read()
            
        b64_data = base64.b64encode(raw_data).decode('utf-8')
        
        chunk_payload = {
            "version": "wrong-version" if args.simulate_incorrect_version and idx==0 else manifest["firmware_version"],
            "index": idx,
            "data": b64_data
        }
        
        chunk_topic = f"ota/firmware/chunk/{idx}"
        print(f"Publishing chunk {idx} to {chunk_topic}...")

        _publish(chunk_payload, chunk_topic)
        if simulate_duplicates and idx % 2 == 0:
            _publish(chunk_payload, chunk_topic)

    if simulate_manifest_delay:
        print("Publishing manifest after the chunks...")
        _publish(manifest_data, manifest_topic)

    print("\nAll OTA update messages published successfully.")
    

if __name__ == "__main__":   
    parser = argparse.ArgumentParser(description="OTA Firmware MQTT Publisher")
    parser.add_argument("--host", "-H", type=str, default="10.43.184.65", 
                        help="IP address of the MQTT broker (default: 10.43.184.65)")
    parser.add_argument("--port", "-p", type=int, default=1883,
                        help="Port of the MQTT broker (default: 1883)")
    parser.add_argument("--file", "-f", type=str, default="firmware.txt", 
                        help="Path to the firmware text file")
    parser.add_argument("--version", "-v", type=str, default="1.2.4", 
                        help="Firmware version string")
    parser.add_argument("--username", "-u", type=str, default="pepito", 
                        help="Username")
    parser.add_argument("--password", type=str, default="pepito2", 
                        help="Password")

    parser.add_argument("--simulate-duplicates", action="store_true", 
                        help="Simulate duplicate chunk messages for testing") 
    parser.add_argument("--simulate-manifest-delay", action="store_true", 
                        help="Simulate a manifest delay") 
    parser.add_argument("--simulate-missing-chunk", action="store_true", 
                        help="Simulate a missing chunk") 
    parser.add_argument("--simulate-incorrect-version", action="store_true", 
                        help="Simulate a chunk with a different version from the manifest") 

    args = parser.parse_args()

    manifest_data = process_firmware(args.file, version=args.version)
    print(json.dumps(manifest_data, indent=4))

    if manifest_data:
        publish_update(manifest_data, args, "ota/firmware/manifest")
