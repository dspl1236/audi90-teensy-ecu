"""
ecu_profiles.py
7A 20v ECU version detection, map addresses, and hardware profiles.

SUPPORTED ECU VERSIONS
======================
893 906 266 B  —  Early 7A, 2-connector (Audi 80/90 Coupe ~1988-1989)
                   Motronic 2.x, no VSS input, no idle switch on ECU connector
893 906 266 D  —  Late 7A, 4-connector (Audi 90/Coupe Quattro ~1990-1991)
                   Motronic 2.x, VSS + knock + extended I/O

DETECTION METHOD
================
1. Reset vector bytes at 0x7FFE-0x7FFF (most reliable)
   266B: BE C7
   266D: 4D 27
2. Region 0x7E00-0x7FAF: 266B = all 0xFF (blank), 266D = programmed
3. CRC32 of first 32KB as fallback fingerprint for known stock ROMs

MAP ADDRESSES (confirmed by stock vs tuned diff analysis)
=========================================================
266D:  Fuel map  0x0000-0x00FF  (16x16 = 256 bytes)
       Timing    0x1000-0x10FF  (16x16 = 256 bytes)
266B:  Fuel map  0x0000-0x00FF  (same layout, different values)
       Timing    0x1000-0x10FF  (same layout)

HARDWARE OPTIONS
================
MAF:
  STOCK_7A    — OEM hotwire 7A MAF, freq intercept ×1.130 for 2.6L
  BIG_MAF     — Bosch 0 280 218 037 in 225mm housing (VR6/S4)
  MAF_18T     — 1.8T MAF (Bosch 0 280 218 091), higher flow range

INJECTORS:
  STOCK_225   — 225cc/min (stock 7A)
  CC440       — 440cc/min
  CC550       — 550cc/min

Each combination has a scalar multiplier applied to the fuel map base values.
"""

from dataclasses import dataclass, field
from typing import Optional
import zlib


# ── ROM address constants ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class MapAddresses:
    fuel_map:   int
    timing_map: int
    map_size:   int = 256   # 16x16


ECU_MAPS = {
    "266B": MapAddresses(fuel_map=0x0000, timing_map=0x1000),
    "266D": MapAddresses(fuel_map=0x0000, timing_map=0x1000),
}

# Reset vectors (bytes at 0x7FFE-0x7FFF in first 32KB half)
RESET_VECTORS = {
    (0xBE, 0xC7): "266B",
    (0x4D, 0x27): "266D",
}

# 266B region 0x7E00-0x7FAF is all 0xFF (unprogrammed on early ECU)
# 266D has real code there
BLANK_REGION_START = 0x7E00
BLANK_REGION_END   = 0x7EFF   # just check first 256 bytes of the blank area

# Known CRC32 fingerprints of stock ROMs (first 32KB)
KNOWN_ROMS = {
    0x35f85c9b: ("266D", "Stock", "893906266D"),
    0x7f722a3c: ("266B", "Stock", "893906266B"),
}


# ── Hardware profiles ─────────────────────────────────────────────────────────

@dataclass
class MAFProfile:
    name:        str
    display:     str
    freq_scalar: float    # multiply intercepted freq by this
    max_flow_hz: int      # max MAF output frequency in Hz
    notes:       str = ""

MAF_PROFILES = {
    "STOCK_7A": MAFProfile(
        name="STOCK_7A",
        display="Stock 7A MAF",
        freq_scalar=1.130,   # 2.6L / 2.3L = 1.130 displacement correction
        max_flow_hz=5000,
        notes="OEM hotwire, freq intercept ×1.130 for 2.6L stroker"
    ),
    "BIG_MAF": MAFProfile(
        name="BIG_MAF",
        display="Big MAF (225mm VR6/S4 housing)",
        freq_scalar=1.0,     # no correction needed, housing calibrated
        max_flow_hz=8000,
        notes="Bosch 0 280 218 037 in 225mm billet housing"
    ),
    "MAF_18T": MAFProfile(
        name="MAF_18T",
        display="1.8T MAF",
        freq_scalar=0.92,    # flow curve correction factor
        max_flow_hz=9500,
        notes="Bosch 0 280 218 091 — requires freq synthesis output"
    ),
}

@dataclass
class InjectorProfile:
    name:       str
    display:    str
    cc_per_min: int
    scalar:     float     # fuel map multiplier relative to stock 225cc
    notes:      str = ""

INJECTOR_PROFILES = {
    "STOCK_225": InjectorProfile(
        name="STOCK_225",
        display="Stock 225cc",
        cc_per_min=225,
        scalar=1.000,
        notes="Factory 7A injectors"
    ),
    "CC440": InjectorProfile(
        name="CC440",
        display="440cc",
        cc_per_min=440,
        scalar=0.511,    # 225/440 = need less fuel per pulse (map values scale down)
        notes="Common 440cc upgrade — e.g. Bosch green top"
    ),
    "CC550": InjectorProfile(
        name="CC550",
        display="550cc",
        cc_per_min=550,
        scalar=0.409,    # 225/550
        notes="034 Stage 2 550cc — matches 034 turbo tune"
    ),
}


# ── Detection ─────────────────────────────────────────────────────────────────

@dataclass
class DetectionResult:
    version:      str            # "266B", "266D", or "UNKNOWN"
    confidence:   str            # "HIGH", "MEDIUM", "LOW"
    method:       str            # how we detected it
    cal_name:     str = ""       # known cal name if matched
    part_number:  str = ""       # e.g. "893906266D"
    crc32:        int = 0
    warnings:     list = field(default_factory=list)


def detect_ecu_version(rom_data: bytes) -> DetectionResult:
    """
    Detect ECU version from raw ROM bytes.
    Returns a DetectionResult with version, confidence, and method used.
    """
    # Work with first 32KB (ROM is mirrored)
    data = rom_data[:0x8000]
    if len(data) < 0x8000:
        data = data + bytes(0x8000 - len(data))

    warnings = []
    crc = zlib.crc32(data) & 0xFFFFFFFF

    # ── Method 1: Known CRC32 fingerprint (highest confidence) ────────────────
    if crc in KNOWN_ROMS:
        ver, cal, pn = KNOWN_ROMS[crc]
        return DetectionResult(
            version=ver, confidence="HIGH",
            method="CRC32 match",
            cal_name=cal, part_number=pn, crc32=crc
        )

    # ── Method 2: Reset vector at 0x7FFE ─────────────────────────────────────
    vec = (data[0x7FFE], data[0x7FFF])
    if vec in RESET_VECTORS:
        ver = RESET_VECTORS[vec]
        return DetectionResult(
            version=ver, confidence="HIGH",
            method=f"Reset vector {vec[0]:02X}{vec[1]:02X} @ 0x7FFE",
            crc32=crc
        )

    # ── Method 3: Check 266B blank region ────────────────────────────────────
    region = data[BLANK_REGION_START:BLANK_REGION_START + 256]
    blank_count = sum(1 for b in region if b == 0xFF)
    if blank_count > 200:
        return DetectionResult(
            version="266B", confidence="MEDIUM",
            method=f"Blank region at 0x{BLANK_REGION_START:04X} ({blank_count}/256 = 0xFF)",
            crc32=crc,
            warnings=["Could not confirm via reset vector — version inferred from blank EPROM region"]
        )
    elif blank_count < 20:
        return DetectionResult(
            version="266D", confidence="MEDIUM",
            method=f"Programmed region at 0x{BLANK_REGION_START:04X} (only {blank_count}/256 = 0xFF)",
            crc32=crc,
            warnings=["Could not confirm via reset vector — version inferred from programmed region"]
        )

    # ── Unknown ───────────────────────────────────────────────────────────────
    return DetectionResult(
        version="UNKNOWN", confidence="LOW",
        method="No match found",
        crc32=crc,
        warnings=[
            "Could not identify ECU version.",
            f"Reset vector: {vec[0]:02X} {vec[1]:02X}",
            f"CRC32: {crc:#010x}",
            "Try uploading the other 034 files or check ROM integrity."
        ]
    )


# ── Fuel map scaling ──────────────────────────────────────────────────────────

def scale_fuel_map(base_map: list, from_injector: str, to_injector: str) -> list:
    """
    Scale a 256-byte fuel map from one injector size to another.
    Values are clamped to 0-255.
    """
    src = INJECTOR_PROFILES.get(from_injector)
    dst = INJECTOR_PROFILES.get(to_injector)
    if not src or not dst:
        return base_map
    # scalar = src_cc / dst_cc  (more cc = need proportionally less map value)
    factor = src.cc_per_min / dst.cc_per_min
    return [max(0, min(255, round(v * factor))) for v in base_map]


def get_maf_scalar(maf_type: str, displacement_ratio: float = 1.130) -> float:
    """Get MAF frequency multiplier for given MAF type and displacement ratio."""
    profile = MAF_PROFILES.get(maf_type)
    if not profile:
        return 1.0
    if maf_type == "STOCK_7A":
        return displacement_ratio   # user-configurable displacement correction
    return profile.freq_scalar


# ── Summary string ────────────────────────────────────────────────────────────

def hardware_summary(maf: str, injectors: str, displacement_cc: int = 2600) -> str:
    m = MAF_PROFILES.get(maf)
    i = INJECTOR_PROFILES.get(injectors)
    if not m or not i:
        return "Unknown config"
    return (
        f"{displacement_cc}cc  |  {m.display}  |  {i.display} injectors  |  "
        f"MAF scalar ×{get_maf_scalar(maf):.3f}"
    )
