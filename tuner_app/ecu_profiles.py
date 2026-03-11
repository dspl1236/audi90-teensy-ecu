"""
ecu_profiles.py  —  7A 20v Tuner  v1.3.0
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
Source: 034 Motorsport RIP Chip .ecu definition files
  7A_Early_Generic_1.06.ecu  (266B)
  7A_Late_Generic_1.01.ecu   (266D)

Address formula: EPROM_addr = dataHighStart_field * 256

CONFIRMED MAP ADDRESSES
=======================
  266B (8 maps):
    0x0000  Fueling Map             16x16 = 256 bytes
    0x0100  Timing Map              16x16 = 256 bytes
    0x0200  RPM / Load axes         16 bytes each
    0x0600  CL Load Limit axis      16 bytes
    0x0700  CL Disable RPM / Inj Scaler  1 byte each
    0x0E00  Decel Cutoff axis       16 bytes
    0x1000  Timing Map Knock        16x16 = 256 bytes
    MAF Linearization 64 bytes also at 0x0200 region

  266D (7 maps):
    0x0000  Primary Fueling         18x16 = 288 bytes  (rows=RPM, cols=Load kPa)
    0x0120  Primary Timing          18x16 = 288 bytes
    0x0240  RPM axis (Y)            18 x 2-byte BE words  @ 0x0260
    0x0250  Load axis (X)           16 x 1-byte values   @ 0x0250
    0x0600  CL Fueling Load         16 bytes (1-D, axis = RPM)
    0x0700  CL RPM Limit            1 byte (scalar)
    0x0701  Fuel Injector Scaler    1 byte (scalar)
    0x0E00  Decel Cutoff            16 bytes (1-D, axis = RPM)
    0x1000  Timing Knock Safety     18x16 = 288 bytes

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
            data_addr=0x0000, xaxis_addr=0x0200, yaxis_addr=0x0200,
            cols=16, rows=16,
            description="Primary fuel map  (Load% x RPM = injector pulse width scalar)"
        ),
        MapDef(
            name="Timing Map",
            data_addr=0x0100, xaxis_addr=0x0200, yaxis_addr=0x0200,
            cols=16, rows=16,
            description="Primary ignition advance map  (degrees BTDC)"
        ),
        MapDef(
            name="Timing Map Knock",
            data_addr=0x1000, xaxis_addr=0x0200, yaxis_addr=0x0200,
            cols=16, rows=16,
            description="Knock safety timing map -- ECU falls back here on knock detection"
        ),
        MapDef(
            name="MAF Linearization",
            data_addr=0x0200, xaxis_addr=0x0000, yaxis_addr=0x0000,
            cols=64, rows=1,
            description="MAF sensor linearization table -- maps raw frequency to load signal"
        ),
        MapDef(
            name="CL Load Limit",
            data_addr=0x0600, xaxis_addr=0x0600, yaxis_addr=0x0000,
            cols=16, rows=1,
            description="Closed-loop O2 feedback: disable above this load threshold (per RPM point)"
        ),
        MapDef(
            name="Injection Scaler",
            data_addr=0x0700, xaxis_addr=0x0000, yaxis_addr=0x0000,
            cols=1, rows=1,
            description="Global injector scaling constant. Larger injector = smaller value. Larger MAF = larger value."
        ),
        MapDef(
            name="CL Disable RPM",
            data_addr=0x0700, xaxis_addr=0x0000, yaxis_addr=0x0000,
            cols=1, rows=1,
            description="Disable all O2 closed-loop feedback above this RPM"
        ),
        MapDef(
            name="Decel Cutoff",
            data_addr=0x0E00, xaxis_addr=0x0E00, yaxis_addr=0x0000,
            cols=16, rows=1,
            description="Injector decel cutoff -- disable injectors below this load threshold per RPM"
        ),
    ],

    "266D": [
        MapDef(
            name="Primary Fueling",
            data_addr=0x0000, xaxis_addr=0x0250, yaxis_addr=0x0260,
            cols=16, rows=18,
            description="Primary fuel map  (RPM × Load kPa = injector pulse width scalar)"
        ),
        MapDef(
            name="Primary Timing",
            data_addr=0x0120, xaxis_addr=0x0250, yaxis_addr=0x0260,
            cols=16, rows=18,
            description="Primary ignition advance map  (degrees BTDC)"
        ),
        MapDef(
            name="Timing Knock Safety",
            data_addr=0x1000, xaxis_addr=0x0250, yaxis_addr=0x0260,
            cols=16, rows=18,
            description="Knock safety timing map -- ECU falls back here on knock detection"
        ),
        MapDef(
            name="CL Fueling Load Threshold",
            data_addr=0x0600, xaxis_addr=0x0600, yaxis_addr=0x0000,
            cols=16, rows=1,
            description="Closed-loop O2: disable above this load threshold per RPM"
        ),
        MapDef(
            name="CL Fueling RPM Limit",
            data_addr=0x0700, xaxis_addr=0x0000, yaxis_addr=0x0000,
            cols=1, rows=1,
            description="Disable closed-loop O2 feedback above this RPM"
        ),
        MapDef(
            name="Fuel Injector Scaler",
            data_addr=0x0701, xaxis_addr=0x0000, yaxis_addr=0x0000,
            cols=1, rows=1,
            description="Global injector scaler constant (bigger injector = smaller value)"
        ),
        MapDef(
            name="Deceleration Cutoff",
            data_addr=0x0E00, xaxis_addr=0x0E00, yaxis_addr=0x0000,
            cols=16, rows=1,
            description="Injector decel cutoff per RPM row"
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
    (0xBE, 0xC7): "266B",
    (0x4D, 0x27): "266D",
}

KNOWN_ROMS = {
    0x35f85c9b: ("266D", "Stock", "893906266D"),
    0x7f722a3c: ("266B", "Stock", "893906266B"),
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
