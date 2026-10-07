import subprocess
import json
import base64
import hashlib
import argparse
import threading
import sys
import os

# Global state variables
manifest = None
received_chunks = {}
timeout_timer = None
sub_process = None

def calculate_sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()

def cleanup_and_exit(code: int):
    """Terminates the mosquitto_sub background process and exits."""
    if sub_process:
        sub_process.terminate()
    sys.exit(code)

    #try:
    #    start_subscriber(args.host, args.port, args.username, args.password)
    #except KeyboardInterrupt:
    #    print("\nDisconnecting...")
    #    cleanup_and_exit(0)

def cleanup():
    global manifest, received_chunks

    manifest = None
    received_chunks.clear()
    print("\n\n Listening for incoming OTA updates...\n")


def fail_update(reason: str):
    """Rejects the update, clears memory, and ensures no corrupt file is saved."""
    print(f"\nUPDATE REJECTED: {reason}")
    print("Clearing received chunks to prevent partial/corrupted states.")
    
    # Ensure the reconstructed file is NOT created/left behind
    if os.path.exists("firmware_reconstructed.txt"):
        os.remove("firmware_reconstructed.txt")
        
    cleanup()

def handle_timeout():
    if manifest and len(received_chunks) < 4:
        fail_update(f"Timeout reached. Expected 4 chunks, only received {len(received_chunks)}.")

def verify_and_reconstruct():
    """Validates the received chunks against the manifest and reconstructs the file."""
    global manifest, received_chunks
    
    print("\nAll 4 chunks received! Starting verification...")
    
    # Confirm exact indices. Shoudnt happen technically
    required_indices = {0, 1, 2, 3}
    if set(received_chunks.keys()) != required_indices:
        fail_update(f"Missing or incorrect chunk indices. Have: {list(received_chunks.keys())}")
        return

    # Confirm exact version
    for idx, chunk_info in received_chunks.items():
        if chunk_info["version"] != manifest.get("firmware_version"):
            fail_update(f"Incorrect version in chunk {idx}.")
            return


    # Reconstruct in strict index order
    ordered_chunks = [received_chunks[i]["bytes"] for i in range(4)]
    reconstructed_data = b"".join(ordered_chunks)
    
    # Check reconstructed size against manifest
    if len(reconstructed_data) != manifest["original_firmware_size_bytes"]:
        fail_update(f"Size mismatch. Expected {manifest['original_firmware_size_bytes']} bytes, got {len(reconstructed_data)} bytes.")
        return

    # Calculate local Merkle root from the chunks
    leaf_hashes = [calculate_sha256(chunk) for chunk in ordered_chunks]
    h_01 = calculate_sha256(leaf_hashes[0] + leaf_hashes[1])
    h_23 = calculate_sha256(leaf_hashes[2] + leaf_hashes[3])
    calculated_root = calculate_sha256(h_01 + h_23).hex()

    # Compare the calculated root with the reference root from the manifest
    if calculated_root != manifest["merkle_root"]:
        #TODO: find broken leaf
        fail_update(f"Merkle root mismatch!\nExpected (from manifest): {manifest['merkle_root']}\nCalculated (from chunks): {calculated_root}")
        return

    with open("firmware_reconstructed.txt", "wb") as f:
        f.write(reconstructed_data)
    
    print("\nUpdate successful! Firmware verified and saved as 'firmware_reconstructed.txt'.")
    print("\nCalculated root:", calculated_root, "== Actual root:", manifest["merkle_root"])
    print("Reconstructed firmware:", reconstructed_data)

    cleanup()

def start_subscriber(host: str, port: int, username: str, password: str):
    global manifest, received_chunks, timeout_timer, sub_process

    command = [
        "mosquitto_sub",
        "-h", host,
        "-p", str(port),
        "-u", username,
        "-P", password,
        "-t", "ota/firmware/#",
        "-q", "1",
        "-v"
    ]

    print(f"Executing background listener: {' '.join(command)}")
    
    try:
        # Start the subprocess with a pipe to read stdout continuously
        sub_process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except FileNotFoundError:
        print("Error: 'mosquitto_sub' command not found")
        sys.exit(1)

    print("Listening for incoming OTA updates...\n")

    for line in iter(sub_process.stdout.readline, ""):
        line = line.strip()
        if not line:
            continue

        # Start a 30-second timeout
        if timeout_timer:
            timeout_timer.cancel()
        timeout_timer = threading.Timer(30.0, handle_timeout)
        timeout_timer.start()

        try:
            topic, payload_str = line.split(" ", 1)
            data = json.loads(payload_str)
        except ValueError:
            print(f"Failed to parse line: {line}")
            continue

        if topic == "ota/firmware/manifest":
            print(f"\nManifest received! Version: {data.get('firmware_version')}")
            manifest = data
            
        elif topic.startswith("ota/firmware/chunk/"):
            idx = data["index"]
            chunk_bytes = base64.b64decode(data["data"])
            
            # Handle duplicates
            if idx not in received_chunks:
                received_chunks[idx] = {
                    "bytes": chunk_bytes,
                    "version": data.get("version")
                }
                print(f"Received chunk {idx} ({len(chunk_bytes)} bytes).")
            else:
                print(f"Ignored duplicate chunk {idx}.")

        # If manifest and exactly 4 chunks arrived, validate
        if len(received_chunks) == 4 and manifest is not None:
            if timeout_timer:
                timeout_timer.cancel()
            verify_and_reconstruct()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Raspberry Pi OTA MQTT Client (Subprocess Version)")
    parser.add_argument("--host", "-H", type=str, default="172.25.61.65", help="MQTT broker IP")
    parser.add_argument("--port", "-p", type=int, default=1883, help="MQTT broker port")
    parser.add_argument("--username", "-u", type=str, default="pepito", help="Username")
    parser.add_argument("--password", type=str, default="pepito2", help="Password")
    args = parser.parse_args()

    try:
        start_subscriber(args.host, args.port, args.username, args.password)
    except KeyboardInterrupt:
        print("\nDisconnecting...")
        cleanup_and_exit(0)