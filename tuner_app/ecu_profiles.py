"""
ecu_profiles.py  —  7A 20v Tuner  v1.4.0
=========================================
ECU version detection, complete map address tables, and hardware profiles.

SUPPORTED ECU VERSIONS
======================
893 906 266 B  —  Early 7A, 2-connector (Audi 80/90/Coupe ~1988-1989)
                   Motronic 2.x  |  8 maps  |  No VSS input
893 906 266 D  —  Late 7A, 4-connector (Audi 90/Coupe Quattro ~1990-1991)
                   Motronic 2.x  |  7 maps  |  VSS + extended I/O

MAP ADDRESS DERIVATION
======================
Source: 034 Motorsport RIP Chip .ecu definition files  +  Java decompilation of
        ECUGUI.jar / CustomizedStore.jar  (034EFI_Rip_Chip_1_0_0_Hitachi.msi)

  7A_Early_Generic_1.06.ecu  (266B)
  7A_Late_Generic_1.01.ecu   (266D)

.034 FILE FORMAT (CRITICAL)
============================
  .034 files are NOT raw EPROM binary dumps!  Every byte is bit-scrambled by
  034EFI RIP Chip software before writing to disk, using the following transform:

    algOne(byte):
      step 1 — algZero: swap odd/even bits
        t1 = byte & 0xAA        # isolate even bits  (positions 7,5,3,1)
        t2 = t1 >> 1            # shift them down
        t3 = byte & 0x55        # isolate odd bits   (positions 6,4,2,0)
        t4 = t3 << 1            # shift them up
        step1 = (t2 | t4) & 0xFF
      step 2 — bSwap: swap high/low nibbles
        result = ((step1 >> 4) | (step1 << 4)) & 0xFF

  To recover the native ECU ROM bytes:  native_byte = algOne(file_byte)
  (algOne is its own inverse — applying it twice returns the original byte)

CONFIRMED MAP ADDRESSES — 266D (from Java reflection dump of 7A_Late_Generic_1.01.ecu)
========================================================================================
  All addresses are in NATIVE ROM space (i.e. after unscrambling algOne transform)

  0x0000  Primary Fueling         16x16 = 256 bytes  (rows=RPM, cols=Load kPa)
  0x0100  Primary Timing          16x16 = 256 bytes
  0x0250  RPM axis (y-axis)       16 bytes  factor=25.0,   offset=0.0
  0x0260  Load axis (x-axis)      16 bytes  factor=0.3922, offset=0.0
  0x0660  CL Fueling Load         16 bytes (1-D)           — dataLowStart=1632
  0x0640  CL Load axis RPM        16 bytes                 — xAxisLowStart=1616
  0x07E1  CL RPM Limit            1 byte (scalar)
  0x0E30  Decel Cutoff            16 bytes (1-D)           — dataLowStart=3632
  0x0E20  Decel Cutoff axis RPM   16 bytes                 — xAxisLowStart=3616
  0x1000  Timing Knock Safety     16x16 = 256 bytes

  Timing axis addresses (shared with Primary Timing):
  0x0270  RPM axis for Timing maps   — yAxisLowStart=624
  0x0280  Load axis for Timing maps  — xAxisLowStart=640

DISPLAY FORMULAS — 266D (confirmed from decompiled ArrayStuff.factorAndOffset)
===============================================================================
  formula:  display = native_byte * dataFactor + dataOffset

  Primary Fueling  (dataSigned=True, dataFactor=1.0, dataOffset=128.0):
    signed_byte = native_byte if native_byte < 128 else native_byte - 256
    display     = signed_byte + 128      range: 0–255 (stock map: ~40–123)
    storage     = round(display - 128)   then store as 2's complement byte

  Primary Timing   (dataSigned=False, dataFactor=1.0, dataOffset=0.0):
    display = native_byte                (degrees BTDC, stock map: ~2–38°)

  RPM axis:   display = native_byte * 25   (stock: 600–6300 RPM)
  Load axis:  display = native_byte * 0.3922  (kPa, stock: ~12.6–100.0)

  NOTE: The axes ARE stored in ROM (not hardcoded). However for the Teensy
        emulator workflow the axis values are read from the ROM file itself.

STOCK MAP DISPLAY RANGES (verified against decoded 034_-_893906266D_Stock.034)
================================================================================
  Fuel map:   40 (WOT decel / low RPM light load) to 123 (high-load enrichment)
  Timing map:  2 (idle / high load retard)         to  38 (part-load advance)
  RPM axis:  600 800 1000 1250 1500 1750 2000 2250 2500 2750 3000 3500 4000 5000 6000 6300
  Load axis: 12.6 18.8 23.5 28.2 32.9 38.8 44.7 50.6 56.9 63.1 69.4 75.7 82.0 88.2 94.5 100.0

CONFIRMED MAP ADDRESSES — 266B (from Java reflection dump of 7A_Early_Generic_1.06.ecu)
========================================================================================
  All addresses are in NATIVE ROM space (after algOne unscramble)

  0x0000  Fueling Map             16x16 = 256 bytes  (twoDInverse=True — display transposes rows/cols)
  0x0100  Timing Map              16x16 = 256 bytes
  0x0250  RPM axis                16 bytes  factor=25.0   (SAME address as 266D)
  0x0260  Load axis               16 bytes  factor=0.3922 (SAME address as 266D)
  0x02D0  MAF Linearization       64 × 16-bit big-endian values (128 bytes)  — 266B ONLY
  0x0660  CL Load Limit           16 bytes (1-D)
  0x0640  CL Load axis RPM        16 bytes
  0x077E  Injection Scaler        1 byte (scalar) — dataLowStart=1918
  0x07E1  CL Disable RPM          1 byte (scalar) — SAME address as 266D
  0x0E30  Decel Cutoff            16 bytes (1-D)
  0x0E20  Decel Cutoff axis RPM   16 bytes
  0x1000  Timing Map Knock        16x16 = 256 bytes

DISPLAY FORMULAS — 266B (confirmed from decompiled source)
===========================================================
  Primary Fueling  (dataSigned=True, dataFactor=0.007813, dataOffset=1.0, decimalPlaces=3):
    display = signed(native_byte) * 0.007813 + 1.0   (Lambda target, ~0.625–0.867 stock)
    1.000 = stoichiometric (14.7:1 AFR),  < 1.0 = rich,  > 1.0 = lean
    storage: native_byte = round((display - 1.0) / 0.007813)  as signed byte

  Primary Timing, Knock Timing: same as 266D (unsigned, degrees BTDC)
  twoDInverse=True for all 266B 16x16 maps — the 034 app displays them transposed.
  In raw ROM the layout is identical (row-major RPM×Load); twoDInverse is a display-only flag.

CHECKSUM (from decompiled Checksum.applyOldStyle) — BOTH ECUs
==============================================================
  Algorithm: sum all bytes of the native 32KB ROM, adjust correction region to hit target.

  266D:  target = 3384576 (0x33A500)  correction region: 0x1600–0x17FF  (512 bytes)
  266B:  target = 3894528 (0x3B6D00)  correction region: 0x1400–0x1FFF  (3072 bytes)

  Both stock ROMs verify with sum == target (confirmed).
  Whenever a map is edited, the correction region bytes must be redistributed
  so the total 32KB byte sum returns to the target value.
  The Teensy emulator serves native ROM bytes directly to the ECU — checksum MUST be valid.

KNOWN ROM CRC32 FINGERPRINTS (first 32KB)
=========================================
  0x35f85c9b  266D  Stock  893906266D
  0x7f722a3c  266B  Stock  893906266B

RESET VECTOR FINGERPRINTS (bytes at 0x7FFE-0x7FFF)
===================================================
  BE C7  ->  266B
  4D 27  ->  266D
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict
import zlib


# ---------------------------------------------------------------------------
# .034 file unscramble  (mandatory before reading any map data)
# Every byte in a .034 file is scrambled by algOne().  Apply to each byte
# to recover the native ECU ROM.  algOne is its own inverse.
# ---------------------------------------------------------------------------

def _alg_zero(b: int) -> int:
    t1 = b & 0xAA
    t2 = t1 >> 1
    t3 = b & 0x55
    t4 = (t3 << 1) & 0xFF
    return (t2 | t4) & 0xFF

def _b_swap(x: int) -> int:
    return ((x >> 4) | ((x << 4) & 0xFF)) & 0xFF

def unscramble_byte(b: int) -> int:
    """Undo 034 EFI bit-scramble: algOne(b) = bSwap(algZero(b))."""
    return _b_swap(_alg_zero(b))

def unscramble_rom(data: bytes) -> bytes:
    """Unscramble a full .034 file to native ECU ROM bytes."""
    return bytes(unscramble_byte(b) for b in data)


# ---------------------------------------------------------------------------
# 266D axis breakpoints — READ FROM ROM (addresses from .ecu definition)
# These defaults are the stock values decoded from 034_-_893906266D_Stock.034
# ---------------------------------------------------------------------------

RPM_AXIS_ADDR_266D  = 0x0250   # yAxisLowStart=592,  factor=25.0
LOAD_AXIS_ADDR_266D = 0x0260   # xAxisLowStart=608,  factor=0.3922

RPM_AXIS_FACTOR_266D  = 25.0
LOAD_AXIS_FACTOR_266D = 0.3922

# Stock defaults (decoded from the stock 034 ROM)
RPM_AXIS_266D  = [600, 800, 1000, 1250, 1500, 1750, 2000, 2250,
                  2500, 2750, 3000, 3500, 4000, 5000, 6000, 6300]

LOAD_AXIS_266D = [12.6, 18.8, 23.5, 28.2, 32.9, 38.8, 44.7, 50.6,
                  56.9, 63.1, 69.4, 75.7, 82.0, 88.2, 94.5, 100.0]  # kPa


def read_rpm_axis_from_rom(native_rom: bytes) -> list:
    """Read 16 RPM breakpoints from native ROM bytes."""
    return [native_rom[RPM_AXIS_ADDR_266D + i] * RPM_AXIS_FACTOR_266D
            for i in range(16)]

def read_load_axis_from_rom(native_rom: bytes) -> list:
    """Read 16 Load (kPa) breakpoints from native ROM bytes."""
    return [round(native_rom[LOAD_AXIS_ADDR_266D + i] * LOAD_AXIS_FACTOR_266D, 1)
            for i in range(16)]


# ---------------------------------------------------------------------------
# 266D Primary Fueling display formula  (confirmed from decompiled source)
#   .034 file byte  →  unscramble_byte()  →  native_byte
#   native_byte is signed (two's complement); dataFactor=1.0, dataOffset=128.0
#   display = signed(native_byte) + 128
#   storage: native_byte = round(display - 128)  stored as uint8 two's complement
# ---------------------------------------------------------------------------

FUEL_DATA_FACTOR  = 1.0
FUEL_DATA_OFFSET  = 128.0
FUEL_DATA_SIGNED  = True


def raw_to_display(raw: int) -> float:
    """Convert native (unscrambled) ECU byte to display fuel value.

    raw: 0-255 unsigned byte from the unscrambled ROM
    Returns display value (stock range: ~40-123)
    """
    signed = raw if raw < 128 else raw - 256
    return float(signed + int(FUEL_DATA_OFFSET))


def display_to_raw(display: float) -> int:
    """Convert display fuel value back to native ROM byte (0-255 uint8)."""
    native_signed = round(display - FUEL_DATA_OFFSET)
    # clamp to signed byte range
    native_signed = max(-128, min(127, native_signed))
    # convert to uint8
    return native_signed & 0xFF


# ---------------------------------------------------------------------------
# 266B Primary Fueling display formula
#   dataFactor=0.007813, dataOffset=1.0, dataSigned=true, decimalPlaces=3
#   display = signed(native_byte) * 0.007813 + 1.0   (Lambda target)
#   1.000 = stoich, 0.868 ≈ rich WOT (~12.75 AFR), stock range: 0.625–0.867
# ---------------------------------------------------------------------------

FUEL_LAMBDA_FACTOR = 0.007813
FUEL_LAMBDA_OFFSET = 1.0


def raw_to_lambda(raw: int) -> float:
    """Convert native 266B ECU byte to Lambda display value."""
    signed = raw if raw < 128 else raw - 256
    return round(signed * FUEL_LAMBDA_FACTOR + FUEL_LAMBDA_OFFSET, 3)


def lambda_to_raw(lam: float) -> int:
    """Convert Lambda display value back to native 266B ROM byte."""
    native_signed = round((lam - FUEL_LAMBDA_OFFSET) / FUEL_LAMBDA_FACTOR)
    native_signed = max(-128, min(127, native_signed))
    return native_signed & 0xFF


# ---------------------------------------------------------------------------
# Checksum  (from decompiled Checksum.applyOldStyle)
#
# Both ECUs use a simple byte-sum checksum over the full 32KB native ROM.
# After any map edit, bytes in the correction region are redistributed
# to bring the total sum back to the target value.
#
# The 034 app spreads the delta evenly across the correction region.
# We replicate that: subtract floor(delta/n) from each byte, put the remainder
# in the last byte, then clamp all bytes to 0-255.
# ---------------------------------------------------------------------------

CHECKSUM_266D = {
    "target":     3384576,   # csFullByteValue
    "cs_from":    0x1600,    # csCorrectionFrom  (512-byte region)
    "cs_to":      0x17FF,    # csCorrectionTo
}
CHECKSUM_266B = {
    "target":     3894528,
    "cs_from":    0x1400,    # 3072-byte region
    "cs_to":      0x1FFF,
}


def verify_checksum(native_rom32k: bytes, version: str) -> bool:
    """Return True if the native 32KB ROM has a valid checksum."""
    cs = CHECKSUM_266D if version == "266D" else CHECKSUM_266B
    return sum(native_rom32k[:32768]) == cs["target"]


def apply_checksum(native_rom32k: bytearray, version: str) -> bytearray:
    """Fix checksum: adjust correction region bytes so sum(32KB ROM) == target.

    Distributes the delta across the correction region one unit at a time,
    skipping any bytes that are already at their clamping limit.  This is
    robust even when per_byte == 0 and the residue is large relative to
    individual byte values.

    Returns a new bytearray of length 32768.
    """
    cs     = CHECKSUM_266D if version == "266D" else CHECKSUM_266B
    target = cs["target"]
    cf     = cs["cs_from"]
    ct     = cs["cs_to"]
    n      = ct - cf + 1

    rom   = bytearray(native_rom32k[:32768])
    delta = sum(rom) - target

    if delta == 0:
        return rom

    sign = 1 if delta > 0 else -1   # +1 means we need to decrease bytes

    remaining = abs(delta)
    passes    = 0
    while remaining > 0:
        passes += 1
        if passes > 256:
            # Pathological: correction region is all 0 or all 255 — shouldn't
            # happen with real ROMs but guard against infinite loop.
            break
        absorbed = 0
        for i in range(n):
            if remaining == 0:
                break
            b = rom[cf + i]
            if sign == 1 and b > 0:        # need to subtract, byte can decrease
                rom[cf + i] -= 1
                absorbed += 1
                remaining -= 1
            elif sign == -1 and b < 255:   # need to add, byte can increase
                rom[cf + i] += 1
                absorbed += 1
                remaining -= 1
        if absorbed == 0:
            break  # completely clamped, give up

    return rom


# ---------------------------------------------------------------------------
# Map definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MapDef:
    """Single map table within an ECU ROM."""
    name:        str
    data_addr:   int        # byte address in EPROM
    xaxis_addr:  int        # 0 = no x-axis (scalar)
    yaxis_addr:  int        # 0 = no y-axis (1-D table)
    cols:        int        # number of columns (x dimension)
    rows:        int        # number of rows (y dimension); 1 = 1-D
    description: str = ""
    editable:    bool = True

    @property
    def size(self) -> int:
        return self.cols * self.rows

    @property
    def is_2d(self) -> bool:
        return self.rows > 1

    @property
    def is_scalar(self) -> bool:
        return self.cols == 1 and self.rows == 1


# ---------------------------------------------------------------------------
# Per-ECU map tables  (addresses from 034 .ecu definition files)
# ---------------------------------------------------------------------------

ECU_MAPS: Dict[str, List[MapDef]] = {

    "266B": [
        MapDef(
            name="Fueling Map",
            data_addr=0x0000, xaxis_addr=0x0260, yaxis_addr=0x0250,
            cols=16, rows=16,
            description="Primary fuel map (RPM × Load). "
                        "display = signed(byte)*0.007813 + 1.0  (Lambda target, stock: 0.625–0.867). "
                        "twoDInverse=True: 034 app transposes display, raw ROM layout is unchanged."
        ),
        MapDef(
            name="Timing Map",
            data_addr=0x0100, xaxis_addr=0x0280, yaxis_addr=0x0270,
            cols=16, rows=16,
            description="Primary ignition advance map (degrees BTDC, stock: 2–38°)"
        ),
        MapDef(
            name="Timing Map Knock",
            data_addr=0x1000, xaxis_addr=0x0280, yaxis_addr=0x0270,
            cols=16, rows=16,
            description="Knock safety timing map"
        ),
        MapDef(
            name="MAF Linearization",
            data_addr=0x02D0, xaxis_addr=0x0000, yaxis_addr=0x0000,
            cols=64, rows=1,
            description="MAF sensor linearization — 64×16-bit big-endian values mapping freq→load signal"
        ),
        MapDef(
            name="Injection Scaler",
            data_addr=0x077E, xaxis_addr=0x0000, yaxis_addr=0x0000,
            cols=1, rows=1,
            description="Global injector scaler (larger injector = smaller value). factor=0.3922"
        ),
        MapDef(
            name="CL Disable RPM",
            data_addr=0x07E1, xaxis_addr=0x0000, yaxis_addr=0x0000,
            cols=1, rows=1,
            description="Disable all O2 closed-loop feedback above this RPM. factor=25"
        ),
        MapDef(
            name="Decel Cutoff",
            data_addr=0x0E30, xaxis_addr=0x0E20, yaxis_addr=0x0000,
            cols=16, rows=1,
            description="Injector decel cutoff — disable injectors below this load per RPM. factor=0.3922"
        ),
        MapDef(
            name="CL Load Limit",
            data_addr=0x0660, xaxis_addr=0x0640, yaxis_addr=0x0000,
            cols=16, rows=1,
            description="Disable O2 closed-loop above this load threshold per RPM"
        ),
    ],

    "266D": [
        MapDef(
            name="Primary Fueling",
            data_addr=0x0000, xaxis_addr=0x0260, yaxis_addr=0x0250,
            cols=16, rows=16,
            description="Primary fuel map  (RPM × Load kPa).  "
                        "display = signed(native_byte) + 128  (stock range: 40–123)"
        ),
        MapDef(
            name="Primary Timing",
            data_addr=0x0100, xaxis_addr=0x0280, yaxis_addr=0x0270,
            cols=16, rows=16,
            description="Primary ignition advance map  (degrees BTDC, stock: 2–38°)"
        ),
        MapDef(
            name="Timing Knock Safety",
            data_addr=0x1000, xaxis_addr=0x0280, yaxis_addr=0x0270,
            cols=16, rows=16,
            description="Knock safety timing map -- ECU falls back here on knock detection"
        ),
        MapDef(
            name="CL Fueling Load Threshold",
            data_addr=0x0660, xaxis_addr=0x0640, yaxis_addr=0x0000,
            cols=16, rows=1,
            description="Closed-loop O2: disable above this load threshold per RPM"
        ),
        MapDef(
            name="CL Fueling RPM Limit",
            data_addr=0x07E1, xaxis_addr=0x0000, yaxis_addr=0x0000,
            cols=1, rows=1,
            description="Disable closed-loop O2 feedback above this RPM  (raw * 25 = RPM)"
        ),
        MapDef(
            name="Fuel Injector Scaler",
            data_addr=0x0000, xaxis_addr=0x0000, yaxis_addr=0x0000,
            cols=1, rows=1,
            description="Global injector scaler constant (bigger injector = smaller value)",
            editable=False  # dataLowStart=0 in .ecu — location TBD
        ),
        MapDef(
            name="Deceleration Cutoff",
            data_addr=0x0E30, xaxis_addr=0x0E20, yaxis_addr=0x0000,
            cols=16, rows=1,
            description="Injector decel cutoff per RPM row  (factor=0.3922)"
        ),
    ],
}


def get_fuel_map_def(version: str) -> MapDef:
    """Return the primary fuel MapDef for this ECU version."""
    for m in ECU_MAPS.get(version, ECU_MAPS["266D"]):
        if "fuel" in m.name.lower() or "fueling" in m.name.lower():
            return m
    return ECU_MAPS["266D"][0]


def get_timing_map_def(version: str) -> MapDef:
    """Return the primary timing MapDef for this ECU version."""
    for m in ECU_MAPS.get(version, ECU_MAPS["266D"]):
        if "timing" in m.name.lower() and "knock" not in m.name.lower():
            return m
    return ECU_MAPS["266D"][1]


# ---------------------------------------------------------------------------
# ECU version detection
# ---------------------------------------------------------------------------

RESET_VECTORS = {
    (0xE8, 0xB1): "266D",   # 893906266D  (7A Late, 4-plug)
    (0xD7, 0xBC): "266B",   # 893906266B  (7A Early, 2-plug)
}

KNOWN_ROMS = {
    # CRC32 of the native (unscrambled) 32KB ROM, lower half of .034 / raw .bin
    0x609f1f40: ("266D", "Stock", "893906266D"),
    0x7739bde5: ("266B", "Stock", "893906266B"),
}

BLANK_REGION_START = 0x7E00   # 266B has 0xFF here; 266D has code


@dataclass
class DetectionResult:
    version:     str
    confidence:  str            # "HIGH" | "MEDIUM" | "LOW"
    method:      str
    cal_name:    str = ""
    part_number: str = ""
    crc32:       int = 0
    warnings:    list = field(default_factory=list)


def detect_ecu_version(rom_data: bytes) -> DetectionResult:
    """
    Identify ECU version from raw ROM bytes.

    Priority order:
      1. CRC32 match against known stock ROMs   -> HIGH confidence
      2. Reset vector at 0x7FFE                 -> HIGH confidence
      3. Blank EPROM region at 0x7E00           -> MEDIUM confidence
    """
    data = rom_data[:0x8000]
    if len(data) < 0x8000:
        data = data + bytes(0x8000 - len(data))

    crc = zlib.crc32(data) & 0xFFFFFFFF

    # Method 1: known CRC
    if crc in KNOWN_ROMS:
        ver, cal, pn = KNOWN_ROMS[crc]
        return DetectionResult(
            version=ver, confidence="HIGH",
            method="CRC32 match",
            cal_name=cal, part_number=pn, crc32=crc
        )

    # Method 2: reset vector
    vec = (data[0x7FFE], data[0x7FFF])
    if vec in RESET_VECTORS:
        return DetectionResult(
            version=RESET_VECTORS[vec], confidence="HIGH",
            method=f"Reset vector {vec[0]:02X}{vec[1]:02X} @ 0x7FFE",
            crc32=crc
        )

    # Method 3: blank EPROM region
    region = data[BLANK_REGION_START:BLANK_REGION_START + 256]
    blank  = sum(1 for b in region if b == 0xFF)
    if blank > 200:
        return DetectionResult(
            version="266B", confidence="MEDIUM",
            method=f"Blank region @ 0x{BLANK_REGION_START:04X} ({blank}/256 = 0xFF)",
            crc32=crc,
            warnings=["Version inferred from blank EPROM area -- could not confirm via reset vector"]
        )
    elif blank < 20:
        return DetectionResult(
            version="266D", confidence="MEDIUM",
            method=f"Programmed region @ 0x{BLANK_REGION_START:04X} ({blank}/256 = 0xFF)",
            crc32=crc,
            warnings=["Version inferred from programmed region -- could not confirm via reset vector"]
        )

    return DetectionResult(
        version="UNKNOWN", confidence="LOW",
        method="No fingerprint matched",
        crc32=crc,
        warnings=[
            "Could not identify ECU version.",
            f"Reset vector bytes: {vec[0]:02X} {vec[1]:02X}",
            f"CRC32: {crc:#010x}",
            "Check 034motorsport.com/downloads for the correct .034 file"
        ]
    )


# ---------------------------------------------------------------------------
# Hardware profiles
# ---------------------------------------------------------------------------

@dataclass
class MAFProfile:
    name:        str
    display:     str
    freq_scalar: float      # base frequency multiplier
    max_flow_hz: int
    notes:       str = ""

MAF_PROFILES: Dict[str, MAFProfile] = {
    "STOCK_7A": MAFProfile(
        name="STOCK_7A",
        display="Stock 7A MAF",
        freq_scalar=1.130,
        max_flow_hz=5000,
        notes="OEM hotwire. Freq intercept x1.130 for 2.6L stroker (adjustable via displacement setting)"
    ),
    "BIG_MAF": MAFProfile(
        name="BIG_MAF",
        display="Big MAF  (225mm VR6/S4 housing)",
        freq_scalar=1.0,
        max_flow_hz=8000,
        notes="Bosch 0 280 218 037 in 225mm billet housing -- no displacement correction needed"
    ),
    "MAF_18T": MAFProfile(
        name="MAF_18T",
        display="1.8T MAF",
        freq_scalar=0.92,
        max_flow_hz=9500,
        notes="Bosch 0 280 218 091 -- requires freq synthesis output on Teensy D21"
    ),
}


@dataclass
class InjectorProfile:
    name:         str
    display:      str
    cc_per_min:   int       # rated flow at rated_bar
    rated_bar:    float     # test pressure the cc rating is given at
    part_number:  str = ""
    notes:        str = ""

    @property
    def cc_at_4bar(self) -> float:
        """Flow normalized to 4.0 bar (stock 7A fuel pressure)."""
        import math
        return round(self.cc_per_min * math.sqrt(4.0 / self.rated_bar), 1)

    @property
    def scalar_from_stock(self) -> float:
        """
        Fuel map multiplier to rescale from stock 7A injectors to this injector.
        Pressure-normalized at 4.0 bar (stock 7A rail pressure).
        scalar = stock_cc_at_4bar / this_cc_at_4bar
        """
        stock_cc = 302.0  # Stock 7A @ 4.0 bar (already at reference pressure)
        return round(stock_cc / self.cc_at_4bar, 4)

    def scalar_from(self, other: "InjectorProfile") -> float:
        """Fuel map multiplier to rescale from other injector to self."""
        return round(other.cc_at_4bar / self.cc_at_4bar, 4)


# Pressure normalization:
#   Stock 7A: Bosch 0 280 150 715  302cc @ 4.0 bar  (already at reference)
#   440cc @ 3.0 bar  -> 440 x sqrt(4.0/3.0) = 508.4cc @ 4.0 bar
#   550cc @ 3.0 bar  -> 550 x sqrt(4.0/3.0) = 635.1cc @ 4.0 bar
#   Scale stock->440:  302 / 508.4 = 0.594
#   Scale stock->550:  302 / 635.1 = 0.476

INJECTOR_PROFILES: Dict[str, InjectorProfile] = {
    "STOCK_7A": InjectorProfile(
        name="STOCK_7A",
        display="Stock 7A  (302cc @ 4.0 bar)",
        cc_per_min=302,
        rated_bar=4.0,
        part_number="Bosch 0 280 150 715",
        notes="Factory 7A injector. 302cc rated @ 4.0 bar. Reference pressure for all scaling."
    ),
    "CC440": InjectorProfile(
        name="CC440",
        display="440cc  (@ 3.0 bar)",
        cc_per_min=440,
        rated_bar=3.0,
        notes="440cc @ 3.0 bar = 508cc @ 4.0 bar.  Fuel map scale vs stock: x0.594"
    ),
    "CC550": InjectorProfile(
        name="CC550",
        display="550cc  (@ 3.0 bar)",
        cc_per_min=550,
        rated_bar=3.0,
        notes="034 Stage 2.  550cc @ 3.0 bar = 635cc @ 4.0 bar.  Fuel map scale vs stock: x0.476"
    ),
}


# ---------------------------------------------------------------------------
# Scaling helpers
# ---------------------------------------------------------------------------

def scale_fuel_map(base_map: list, from_injector: str, to_injector: str) -> list:
    """
    Rescale a fuel map from one injector to another.
    Uses pressure-normalized flow (cc @ 4.0 bar) for both injectors.
    factor = from_cc_at_4bar / to_cc_at_4bar
    """
    src = INJECTOR_PROFILES.get(from_injector)
    dst = INJECTOR_PROFILES.get(to_injector)
    if not src or not dst or from_injector == to_injector:
        return list(base_map)
    factor = src.cc_at_4bar / dst.cc_at_4bar
    return [max(0, min(255, round(v * factor))) for v in base_map]


def get_maf_scalar(maf_type: str, displacement_cc: int = 2600) -> float:
    """
    Return MAF frequency multiplier.
    For STOCK_7A: scalar = displacement_cc / 2300 (stock engine displacement).
    """
    profile = MAF_PROFILES.get(maf_type)
    if not profile:
        return 1.0
    if maf_type == "STOCK_7A":
        return round(displacement_cc / 2300.0, 4)
    return profile.freq_scalar


def hardware_summary(maf: str, injectors: str, displacement_cc: int = 2600) -> str:
    m = MAF_PROFILES.get(maf)
    i = INJECTOR_PROFILES.get(injectors)
    if not m or not i:
        return "Unknown config"
    scalar = get_maf_scalar(maf, displacement_cc)
    return (
        f"{displacement_cc}cc  |  {m.display}  |  {i.display} injectors  "
        f"|  MAF scalar x{scalar:.4f}"
    )


# ---------------------------------------------------------------------------
# Known ROM library  (filenames from 034motorsport.com/downloads)
# ---------------------------------------------------------------------------

@dataclass
class KnownROM:
    filename:   str
    version:    str
    stage:      str
    maf:        str
    injectors:  str
    notes:      str

KNOWN_ROM_LIBRARY: List[KnownROM] = [
    # 266B -- Early 2-connector ECU
    KnownROM("034 - 893906266B Stock",                             "266B", "Stock",   "STOCK_7A", "STOCK_7A", "OEM stock"),
    KnownROM("034 - 893906266B - NA Big MAF 91Oct R2",             "266B", "NA",      "BIG_MAF",  "STOCK_7A", "NA Big MAF 91oct"),
    KnownROM("034 - 893906266B - Stage 1 91Oct R1",                "266B", "Stage1",  "STOCK_7A", "STOCK_7A", "Stage 1 NA"),
    KnownROM("034 - 893906266B - Stage 1 91 Octane Turbo",         "266B", "TurboS1", "STOCK_7A", "STOCK_7A", "Turbo Stage 1"),
    KnownROM("034 - 893906266B - Stage 2 91 Octane 550cc Turbo",   "266B", "TurboS2", "STOCK_7A", "CC550",     "Turbo Stage 2 550cc"),
    # 266D -- Late 4-connector ECU
    KnownROM("034 - 893906266D Stock",                             "266D", "Stock",   "STOCK_7A", "STOCK_7A", "OEM stock"),
    KnownROM("034 - 893906266D Turbo Stage 2 550cc",               "266D", "TurboS2", "STOCK_7A", "CC550",     "Turbo Stage 2 550cc"),
    KnownROM("034 - 893906266D Big MAF 91Oct R1",                  "266D", "NA",      "BIG_MAF",  "STOCK_7A", "NA Big MAF"),
    KnownROM("034 - 893906266D Stage 1 91Oct R1",                  "266D", "Stage1",  "STOCK_7A", "STOCK_7A", "Stage 1 NA"),
    KnownROM("034 Turbo Kit Stage 1 91 R2 893906266B",             "266B", "TurboS1", "STOCK_7A", "STOCK_7A", "Turbo Stage 1 R2"),
]
