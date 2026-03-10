"""
serial_comm/rom_transfer.py
Binary ROM upload/download over USB serial.

PROTOCOL EXTENSION
==================
PC → TEENSY:
  CMD:ROM_DOWNLOAD,filename\n
    → Teensy replies with binary transfer header then chunks:
    XFER:START,filename,size\n
    <size bytes of raw binary in 256-byte chunks, each preceded by:>
    XFER:CHUNK,index,length\n<length bytes>\n
    XFER:END,crc32\n

  CMD:ROM_UPLOAD,filename,size\n
    → Teensy replies: ACK:ROM_UPLOAD_READY\n
    PC then sends chunks:
    XFER:CHUNK,index,length\n<length bytes>\n
    After all chunks: XFER:DONE,crc32\n
    → Teensy replies: ACK:ROM_UPLOAD_COMPLETE or ERR:ROM_UPLOAD_CRC

CHUNK SIZE: 256 bytes
MAX ROM SIZE: 65536 bytes (64KB)
"""

import struct
import zlib
import threading
import time
from typing import Optional, Callable


CHUNK_SIZE = 256
MAX_ROM_SIZE = 65536


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


class RomDownloader:
    """
    Downloads a ROM binary from Teensy SD card over USB serial.
    Runs in a background thread, calls progress_cb(bytes_done, total)
    and complete_cb(data: bytes) or error_cb(msg: str) when done.
    """
    def __init__(self, teensy_serial,
                 filename: str,
                 progress_cb: Callable,
                 complete_cb: Callable,
                 error_cb: Callable):
        self.ser        = teensy_serial
        self.filename   = filename
        self.progress_cb = progress_cb
        self.complete_cb = complete_cb
        self.error_cb   = error_cb
        self._buf       = bytearray()
        self._total     = 0
        self._chunks    = {}
        self._started   = False
        self._done      = False

    def start(self):
        self.ser.send_command(f"CMD:ROM_DOWNLOAD,{self.filename}")

    def feed_line(self, line: str) -> bool:
        """
        Feed a line from the serial reader. Returns True if this transfer
        is complete (success or error). Call from the serial reader thread.
        """
        if line.startswith("XFER:START,"):
            parts = line[11:].split(",")
            self.filename = parts[0]
            self._total   = int(parts[1])
            self._started = True
            self.progress_cb(0, self._total)
            return False

        elif line.startswith("XFER:CHUNK,"):
            # Next read will be raw bytes — handled by feed_chunk()
            parts = line[11:].split(",")
            self._pending_chunk_idx = int(parts[0])
            self._pending_chunk_len = int(parts[1])
            return False

        elif line.startswith("XFER:END,"):
            expected_crc = int(line[9:])
            data = bytearray(self._total)
            for idx, chunk in sorted(self._chunks.items()):
                offset = idx * CHUNK_SIZE
                data[offset:offset + len(chunk)] = chunk
            actual_crc = crc32(bytes(data))
            if actual_crc == expected_crc:
                self.complete_cb(bytes(data))
            else:
                self.error_cb(f"CRC mismatch: expected {expected_crc:#010x}, got {actual_crc:#010x}")
            self._done = True
            return True

        elif line.startswith("ERR:"):
            self.error_cb(line[4:])
            self._done = True
            return True

        return False

    def feed_chunk(self, data: bytes):
        """Feed raw chunk bytes (called after a XFER:CHUNK header line)."""
        idx = self._pending_chunk_idx
        self._chunks[idx] = data
        done = sum(len(c) for c in self._chunks.values())
        self.progress_cb(done, self._total)


class RomUploader:
    """
    Uploads a ROM binary from PC to Teensy SD card over USB serial.
    """
    def __init__(self, teensy_serial,
                 filename: str,
                 data: bytes,
                 progress_cb: Callable,
                 complete_cb: Callable,
                 error_cb: Callable):
        self.ser         = teensy_serial
        self.filename    = filename
        self.data        = data
        self.progress_cb = progress_cb
        self.complete_cb = complete_cb
        self.error_cb    = error_cb
        self._ready      = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Send upload request. Wait for ACK:ROM_UPLOAD_READY then send chunks."""
        size = len(self.data)
        self.ser.send_command(f"CMD:ROM_UPLOAD,{self.filename},{size}")

    def on_ready(self):
        """Called when Teensy sends ACK:ROM_UPLOAD_READY."""
        self._thread = threading.Thread(target=self._send_chunks, daemon=True)
        self._thread.start()

    def _send_chunks(self):
        data   = self.data
        total  = len(data)
        offset = 0
        idx    = 0
        try:
            while offset < total:
                chunk = data[offset:offset + CHUNK_SIZE]
                # Send chunk header
                self.ser._serial.write(
                    f"XFER:CHUNK,{idx},{len(chunk)}\n".encode("ascii")
                )
                # Send raw bytes
                self.ser._serial.write(chunk)
                self.ser._serial.write(b"\n")
                self.ser._serial.flush()
                offset += len(chunk)
                idx    += 1
                self.progress_cb(offset, total)
                time.sleep(0.005)   # 5ms pacing — gives Teensy time to write SD

            # Send completion with CRC
            crc = crc32(data)
            self.ser.send_command(f"XFER:DONE,{crc}")
        except Exception as e:
            self.error_cb(str(e))

    def feed_line(self, line: str) -> bool:
        if line == "ACK:ROM_UPLOAD_READY":
            self.on_ready()
            return False
        elif line == "ACK:ROM_UPLOAD_COMPLETE":
            self.complete_cb()
            return True
        elif line.startswith("ERR:ROM_UPLOAD"):
            self.error_cb(line)
            return True
        return False
