#!/usr/bin/env python3
"""
TeensyEprom — Desktop ROM Manager

Upload, download, and switch ROM files on the TeensyEprom via USB serial.
Requires: pip install pyserial

Usage:
    python teensy_eprom.py                     # interactive mode
    python teensy_eprom.py info                # firmware info
    python teensy_eprom.py list                # list maps on device
    python teensy_eprom.py upload FILE.bin     # upload ROM file
    python teensy_eprom.py download INDEX OUT  # download ROM to file
    python teensy_eprom.py switch INDEX        # switch active map
    python teensy_eprom.py delete INDEX        # delete map from flash
    python teensy_eprom.py format              # delete all maps
    python teensy_eprom.py port PORT CMD...    # specify serial port
"""

import sys
import os
import time
import struct
import argparse

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("ERROR: pyserial required — pip install pyserial")
    sys.exit(1)


BAUD = 115200
TIMEOUT = 3.0
UPLOAD_CHUNK = 4096


# ---------------------------------------------------------------------------
# CRC16-CCITT (match firmware)
# ---------------------------------------------------------------------------

def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


# ---------------------------------------------------------------------------
# Port auto-detection
# ---------------------------------------------------------------------------

def find_teensy_port() -> str:
    """Find the first Teensy serial port."""
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "").lower()
        vid = p.vid or 0
        # Teensy USB: VID 0x16C0
        if vid == 0x16C0 or "teensy" in desc:
            return p.device
    # Fallback: first USB serial port
    for p in serial.tools.list_ports.comports():
        if "usb" in (p.description or "").lower():
            return p.device
    return None


# ---------------------------------------------------------------------------
# Serial communication
# ---------------------------------------------------------------------------

class TeensyEprom:
    def __init__(self, port=None):
        if port is None:
            port = find_teensy_port()
            if port is None:
                raise RuntimeError("No Teensy found — specify port with 'port PORT'")
        self.ser = serial.Serial(port, BAUD, timeout=TIMEOUT)
        # Flush any startup banner
        time.sleep(0.3)
        self.ser.reset_input_buffer()

    def close(self):
        self.ser.close()

    def send_cmd(self, cmd: str) -> list[str]:
        """Send text command, return response lines."""
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\n").encode())
        self.ser.flush()

        lines = []
        deadline = time.time() + TIMEOUT
        while time.time() < deadline:
            raw = self.ser.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                lines.append(line)
        return lines

    def info(self):
        for line in self.send_cmd("INFO"):
            print(line)

    def list_maps(self):
        for line in self.send_cmd("LIST"):
            print(line)

    def switch(self, idx: int):
        for line in self.send_cmd(f"MAP {idx}"):
            print(line)

    def dump(self):
        for line in self.send_cmd("DUMP"):
            print(line)

    def delete(self, idx: int):
        for line in self.send_cmd(f"DELETE {idx}"):
            print(line)

    def format_flash(self):
        resp = input("This will delete ALL maps from flash. Type 'yes' to confirm: ")
        if resp.strip().lower() != "yes":
            print("Cancelled.")
            return
        for line in self.send_cmd("FORMAT"):
            print(line)

    def scan(self):
        for line in self.send_cmd("SCAN"):
            print(line)

    def upload(self, filepath: str):
        """Upload a .bin ROM file to the TeensyEprom."""
        if not os.path.exists(filepath):
            print(f"ERROR: file not found: {filepath}")
            return False

        with open(filepath, "rb") as f:
            data = f.read()
        size = len(data)
        filename = os.path.basename(filepath)

        if size < 32768:
            print(f"ERROR: file too small ({size}B, need >= 32768)")
            return False
        if size > 65536:
            print(f"ERROR: file too large ({size}B, max 65536)")
            return False

        # Verify checksum (mod256) as a sanity check
        lower = data[:32768]
        mod = sum(lower) % 256
        if mod != 0:
            print(f"WARNING: ROM checksum mod256 = 0x{mod:02X} (expected 0x00)")
            print("         File may not be a valid ROM. Upload anyway? [y/N] ", end="")
            if input().strip().lower() != "y":
                print("Cancelled.")
                return False

        crc = crc16_ccitt(data)

        print(f"Uploading {filename} ({size}B, CRC 0x{crc:04X})...")

        # Send UPLOAD command
        self.ser.reset_input_buffer()
        self.ser.write(f"UPLOAD {filename} {size}\n".encode())
        self.ser.flush()

        # Wait for READY
        deadline = time.time() + TIMEOUT
        ready = False
        while time.time() < deadline:
            line = self.ser.readline().decode("utf-8", errors="replace").rstrip()
            if line.startswith("ERR"):
                print(f"ERROR: {line}")
                return False
            if line == "READY":
                ready = True
                break

        if not ready:
            print("ERROR: no READY response from device")
            return False

        # Send raw data + CRC16
        sent = 0
        while sent < size:
            chunk = min(UPLOAD_CHUNK, size - sent)
            self.ser.write(data[sent:sent + chunk])
            sent += chunk
            pct = sent * 100 // size
            print(f"\r  Sending... {pct}% ({sent}/{size})", end="", flush=True)

        # Send CRC16 (big-endian)
        self.ser.write(struct.pack(">H", crc))
        self.ser.flush()
        print()

        # Wait for result
        deadline = time.time() + TIMEOUT
        while time.time() < deadline:
            line = self.ser.readline().decode("utf-8", errors="replace").rstrip()
            if line:
                print(f"  {line}")
                if line.startswith("OK") or line.startswith("ERR"):
                    return line.startswith("OK")

        print("ERROR: no response after upload")
        return False

    def download(self, idx: int, outpath: str):
        """Download a ROM from the device to a local file."""
        self.ser.reset_input_buffer()
        self.ser.write(f"DOWNLOAD {idx}\n".encode())
        self.ser.flush()

        # Read SIZE line
        deadline = time.time() + TIMEOUT
        size = None
        while time.time() < deadline:
            line = self.ser.readline().decode("utf-8", errors="replace").rstrip()
            if line.startswith("ERR"):
                print(f"ERROR: {line}")
                return False
            if line.startswith("SIZE "):
                size = int(line.split()[1])
                break

        if size is None:
            print("ERROR: no SIZE response")
            return False

        # Read raw data + CRC16
        data = self.ser.read(size)
        if len(data) != size:
            print(f"ERROR: received {len(data)}/{size} bytes")
            return False

        crc_bytes = self.ser.read(2)
        if len(crc_bytes) != 2:
            print("ERROR: CRC not received")
            return False

        crc_rx = struct.unpack(">H", crc_bytes)[0]
        crc_calc = crc16_ccitt(data)

        if crc_rx != crc_calc:
            print(f"ERROR: CRC mismatch (got 0x{crc_rx:04X}, calc 0x{crc_calc:04X})")
            return False

        with open(outpath, "wb") as f:
            f.write(data)

        print(f"OK: {outpath} ({size}B, CRC 0x{crc_calc:04X})")
        return True


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

def interactive(te: TeensyEprom):
    print("TeensyEprom — interactive mode (type 'help' for commands, 'quit' to exit)")
    while True:
        try:
            cmd = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not cmd:
            continue
        if cmd in ("quit", "exit", "q"):
            break
        elif cmd == "help":
            print("  info              — device info")
            print("  list              — list maps")
            print("  switch N          — switch to map N")
            print("  upload FILE.bin   — upload ROM file")
            print("  download N FILE   — download map N to file")
            print("  delete N          — delete map N")
            print("  format            — delete all maps")
            print("  dump              — hex dump first 256 bytes")
            print("  scan              — rescan maps")
            print("  quit              — exit")
        elif cmd == "info":
            te.info()
        elif cmd == "list":
            te.list_maps()
        elif cmd.startswith("switch "):
            te.switch(int(cmd.split()[1]))
        elif cmd.startswith("upload "):
            te.upload(cmd.split(None, 1)[1])
        elif cmd.startswith("download "):
            parts = cmd.split()
            if len(parts) >= 3:
                te.download(int(parts[1]), parts[2])
            else:
                print("Usage: download INDEX OUTFILE")
        elif cmd.startswith("delete "):
            te.delete(int(cmd.split()[1]))
        elif cmd == "format":
            te.format_flash()
        elif cmd == "dump":
            te.dump()
        elif cmd == "scan":
            te.scan()
        else:
            # Pass through as raw serial command
            for line in te.send_cmd(cmd):
                print(line)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="TeensyEprom ROM Manager")
    parser.add_argument("--port", "-p", help="Serial port (auto-detect if omitted)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("info", help="Device info")
    sub.add_parser("list", help="List maps on device")
    sub.add_parser("dump", help="Hex dump active ROM")
    sub.add_parser("scan", help="Rescan maps")
    sub.add_parser("format", help="Delete all maps")

    p_sw = sub.add_parser("switch", help="Switch active map")
    p_sw.add_argument("index", type=int)

    p_up = sub.add_parser("upload", help="Upload ROM file")
    p_up.add_argument("file", help=".bin ROM file")

    p_dl = sub.add_parser("download", help="Download map to file")
    p_dl.add_argument("index", type=int)
    p_dl.add_argument("outfile", help="Output file path")

    p_rm = sub.add_parser("delete", help="Delete map from flash")
    p_rm.add_argument("index", type=int)

    args = parser.parse_args()

    try:
        te = TeensyEprom(port=args.port)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    try:
        if args.command is None:
            interactive(te)
        elif args.command == "info":
            te.info()
        elif args.command == "list":
            te.list_maps()
        elif args.command == "switch":
            te.switch(args.index)
        elif args.command == "upload":
            te.upload(args.file)
        elif args.command == "download":
            te.download(args.index, args.outfile)
        elif args.command == "delete":
            te.delete(args.index)
        elif args.command == "dump":
            te.dump()
        elif args.command == "scan":
            te.scan()
        elif args.command == "format":
            te.format_flash()
    finally:
        te.close()


if __name__ == "__main__":
    main()
