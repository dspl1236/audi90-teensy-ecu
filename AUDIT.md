# TeensyEprom — Project Audit

**Date:** 2026-03-26
**Scope:** Full audit — firmware, desktop app, hardware design, PCB, CI/CD, documentation

---

## Executive Summary

TeensyEprom is a well-engineered EPROM emulator at v2.0 maturity. The core concept is
sound, the code is clean (~1,140 LOC total), and the documentation is thorough. The
project is production-ready for its primary use case (breadboard/hand-wired builds).

The KiCad PCB files are **templates only** — board outline and net definitions exist but
no component footprints are placed and no traces are routed. This is the main gap blocking
a manufactured PCB.

Below are findings organized by severity: critical, recommended, and nice-to-have.

---

## Critical Issues

### 1. /OE and /CE level clamping relies on undocumented internal diodes

**File:** `LITE_BUILD.md`, `config.h`, `BOARD_DESIGN.md`

The design uses 1kΩ series resistors on /OE and /CE, relying on the IMXRT1062's internal
ESD clamping diodes to limit 5V input to 3.3V. While this works (clamp current is ~1mA,
within typical ESD diode capacity), the IMXRT1062 datasheet **does not specify or guarantee**
these clamping diodes for continuous current flow.

**Risk:** Possible GPIO damage over extended operation, especially at elevated temperatures
in an engine bay environment.

**Fix options:**
- **(A) Route /OE and /CE through spare 74HCT245 channels** — the 245 has 8 channels per IC,
  and only 8 are used on each. Route /OE through U1's unused B-side, /CE through U2's.
  This is free (no extra parts).
- **(B) Use voltage dividers** — replace the 1kΩ series resistors with 3.3kΩ + 6.8kΩ dividers.
  Output: 5V × 6.8/(3.3+6.8) = 3.37V. Within spec for 3.3V GPIO (max 3.6V abs).
- **(C) Use a dedicated level shifter** for the two control signals.

**Recommendation:** Option (A) — zero additional cost, fully reliable.

### 2. `led_error()` halts the system with no USB recovery path

**File:** `firmware/src/main.cpp:230-235`

```cpp
static void led_error() {
    while (true) {  // <-- infinite loop, USB commands unreachable
        digitalWriteFast(PIN_LED, HIGH); delay(50);
        digitalWriteFast(PIN_LED, LOW);  delay(50);
    }
}
```

If `load_active_map()` fails at boot (corrupted file, flash error), the device becomes a
brick until power-cycled. The user cannot upload a new map via USB.

**Fix:** Replace infinite loop with a timed error indication, then fall through to the
main loop where USB commands are processed:

```cpp
static void led_error_signal() {
    led_blink(10, 50);  // 10 fast blinks = error
}
```

### 3. Data bus has no protection against 5V back-drive

**File:** `LITE_BUILD.md` — Data Bus section

Teensy 3.3V GPIO outputs drive the ECU data bus directly. If the ECU has internal pull-ups
to 5V on the data lines (some Motronic variants do), or during programming-mode pin states,
5V could be applied to Teensy outputs configured as INPUT (hi-Z). This exceeds the GPIO
absolute maximum rating.

**Fix options:**
- **(A) Add a 3rd 74HCT245 for the data bus** — direction controlled by /OE. Provides
  proper 5V output levels AND tri-state control AND input protection. Cost: ~$0.50.
- **(B) Add 100Ω series resistors** on each data line — limits back-drive current to safe levels.

**Recommendation:** Option (A) for PCB design. Option (B) for breadboard builds.

---

## Recommended Improvements

### 4. ISR uses pin-by-pin GPIO reads (slow)

**File:** `firmware/src/main.cpp:37-43`

```cpp
FASTRUN static inline uint16_t read_address() {
    uint16_t addr = 0;
    for (uint8_t i = 0; i < 16; i++) {
        if (digitalReadFast(ADDR_PINS[i])) addr |= (1u << i);
    }
    return addr;
}
```

Each `digitalReadFast()` compiles to a GPIO port register read + mask. With 16 pins across
multiple GPIO ports, this is ~16 register reads. On Teensy 4.1, GPIO pins map to registers
GPIO6, GPIO7, GPIO8, GPIO9 (fast access). Reading these registers directly and
bit-manipulating would reduce ISR time from ~250ns to ~50-100ns.

**Impact:** More timing margin, especially for faster ECU bus cycles.

**Fix:** Direct port register reads with precomputed bitmasks. See the optimization
section at the end of this document.

### 5. Arduino `String` class in serial command handler

**File:** `firmware/src/main.cpp:582-594`

```cpp
static void usb_read() {
    static String buf;  // <-- heap-allocated, can fragment
    // ...
    buf += c;           // <-- may reallocate on every character
}
```

The `String` class performs dynamic heap allocation. For a device that may run continuously
for months in an ECU, heap fragmentation could eventually cause `malloc` failures.

**Fix:** Use a fixed `char` buffer:
```cpp
static char buf[128];
static uint8_t buf_len = 0;
```

### 6. `cmd_download()` / `cmd_delete()` — `toInt()` returns 0 for invalid input

**File:** `firmware/src/main.cpp:459, 500`

`args.toInt()` silently returns 0 for non-numeric input (e.g., `DELETE foo`), which would
operate on map index 0 instead of reporting an error.

**Fix:** Validate that the argument is actually numeric before converting.

### 7. Desktop app file handle leak

**File:** `app/teensy_eprom.py:145`

```python
data = open(filepath, "rb").read()  # file handle never explicitly closed
```

**Fix:** Use `with` statement:
```python
with open(filepath, "rb") as f:
    data = f.read()
```

### 8. No bulk decoupling capacitor

Only 100nF bypass caps are specified. The Teensy 4.1 draws ~100mA at 600MHz. During
startup, current surges can cause voltage dips on the 5V rail from the ECU.

**Fix:** Add a 10µF electrolytic or ceramic cap between Vin and GND, close to the Teensy.

### 9. `volatile` cast-away in `load_rom_from_file()`

**File:** `firmware/src/main.cpp:121`

```cpp
f.read((uint8_t*)g_rom, ROM_ACTIVE_SIZE);  // casts away volatile
```

`g_rom` is `volatile uint8_t[]`. Casting to `uint8_t*` is technically undefined behavior.
In practice this works on ARM GCC, but it's not standards-compliant.

**Fix:** Read into a temporary buffer, then copy:
```cpp
uint8_t tmp[ROM_ACTIVE_SIZE];
f.read(tmp, ROM_ACTIVE_SIZE);
memcpy((void*)g_rom, tmp, ROM_ACTIVE_SIZE);
```
Or use `volatile`-aware copy, or remove `volatile` from `g_rom` and use a memory barrier
after loading.

---

## Nice-to-Have

### 10. BOM discrepancy: LITE_BUILD.md says DIP-20, BOARD_DESIGN.md says SOIC-20

`LITE_BUILD.md` specifies `SN74HCT245N (DIP-20)` for breadboard builds.
`BOARD_DESIGN.md` specifies `SOIC-20` for PCB. Both are correct for their context,
but it should be explicitly noted that the PCB version uses different packages.

### 11. Pin 27 handling on 27C256

On 27C256, pin 27 is /PGM (should be tied HIGH via pull-up or to Vcc). The current design
routes it through U2 as A14. On a 256-based ECU, pin 27 may float. The 74HCT245 input
has a weak pull-up tendency but no guaranteed pull-up. Adding a 10kΩ pull-up to Vcc on
pin 27 would ensure clean operation on both chip types.

### 12. KiCad schematic uses text annotations instead of proper symbols

The `.kicad_sch` file uses `text` and `net_label` elements rather than proper component
symbols. This means KiCad's DRC (Design Rule Check) and ERC (Electrical Rules Check)
cannot validate the design. For a production PCB, proper symbols should be used.

### 13. No watchdog timer

The firmware has no watchdog. If the ISR hangs (e.g., /OE stuck LOW), the device becomes
unresponsive. Teensy 4.1 has a hardware watchdog that could reset the system if the main
loop stops executing.

### 14. CI workflow hardcodes Python 3.11

**File:** `.github/workflows/build.yml:44`

Should use `python-version: "3.x"` or a matrix for forward compatibility.

---

## PCB Design Assessment

### Current State

The KiCad files contain:
- Board outline (65mm × 28mm, rounded corners)
- Net definitions (30 nets: GND, +5V, +3V3, A0-A15, D0-D7, OE, CE, BTN)
- GND zone fill on back copper
- Silkscreen text labels
- Placement reference circles on Dwgs.User layer

**Missing:**
- Component footprints (no pads for any component)
- Copper traces (no routing)
- Drill holes
- Proper solder mask openings
- Via definitions
- Footprint associations in schematic

### Single vs Double PCB Stack

**Single board (current design):**
```
┌─────────────────────────────┐
│ [245s] [Teensy via headers] │  ← top: all components
│ [DIP-28 pins through board] │
└─────────────────────────────┘
      ↓ pins into ECU socket
```
- Simpler, cheaper ($2 for 5 boards)
- ~15mm total height above ECU
- All routing on one board
- Tight fit (28mm width for 245 SOIC-20 + Teensy headers)

**Double stack:**
```
┌─────────────────────────────┐
│ [Teensy plugs in here]      │  ← Top board: Teensy + button
│ [245s] [R] [C]              │
├─────────────────────────────┤
│ Board-to-board headers      │  ← Inter-board connection
├─────────────────────────────┤
│ [DIP-28 pins]               │  ← Bottom board: socket interface only
└─────────────────────────────┘
      ↓ pins into ECU socket
```
- Bottom board can be exactly DIP-28 footprint size (~38mm × 15mm)
- Top board can be wider for easier routing
- More clearance for different ECU socket depths
- ~20-22mm total height
- More complex, 2 boards to fabricate
- Board-to-board connector adds cost and potential failure point

**Recommendation:** Single board is preferred for this application. The 28mm width is
workable with SOIC-20 packages, and the single-board approach has fewer failure modes
(no inter-board connector in a vibrating engine bay).

---

## Suggested Architecture Improvements

### Option A: Minimal changes (fix critical issues only)

Keep the current design. Changes:
1. Route /OE and /CE through spare 74HCT245 channels (no new parts)
2. Fix `led_error()` to allow USB recovery
3. Add bulk decoupling cap to BOM
4. Replace `String` with fixed buffer in serial handler

### Option B: Recommended upgrade (add data bus buffer)

Everything in Option A, plus:
1. Add U3: 74HCT245 for data bus (Teensy → ECU direction, /OE controls tri-state)
2. Direct GPIO port register reads for address bus
3. Add watchdog timer

**Revised circuit with 3x 74HCT245:**
```
DIP-28 → U1 (A0-A7, /OE via spare channel)  → Teensy pins 2-9
DIP-28 → U2 (A8-A15, /CE via spare channel) → Teensy pins 10-12, 24-28
Teensy → U3 (D0-D7, /OE controls direction) → DIP-28 data pins
```

U3 wiring:
- Vcc = 5V (NOT 3.3V — so outputs are full 5V TTL swing)
- DIR = HIGH (B→A, Teensy→ECU)
- /OE = connected to /OE from DIP-28 (active LOW — data driven only when ECU reads)
- A-side = DIP-28 data pins (5V)
- B-side = Teensy data pins (3.3V input is fine for HCT)

This gives:
- Full 5V data bus drive (better noise margin)
- Automatic tri-state when not being read
- Protection of Teensy data pins from 5V
- Only adds ~$0.50 to BOM

### Option C: Maximum performance (FlexIO DMA)

Everything in Option B, plus:
1. FlexIO-based parallel bus emulation (zero CPU overhead)
2. DMA transfer from ROM buffer to data bus
3. Hardware-timed response to /OE

This is a significant firmware rewrite but would achieve sub-50ns response times and
free the CPU entirely for other tasks (logging, USB, corrections).

---

## GPIO Port Register Optimization (reference)

Teensy 4.1 pin-to-register mapping for address bus pins:

| Teensy Pin | IMXRT Pad | GPIO Register | Bit |
|------------|-----------|---------------|-----|
| 2 | EMC_04 | GPIO4 | 4 |
| 3 | EMC_05 | GPIO4 | 5 |
| 4 | EMC_06 | GPIO4 | 6 |
| 5 | EMC_08 | GPIO4 | 8 |
| 6 | B0_10 | GPIO2 | 10 |
| 7 | B1_01 | GPIO2 | 17 |
| 8 | B1_00 | GPIO2 | 16 |
| 9 | B0_11 | GPIO2 | 11 |
| 10 | B0_00 | GPIO2 | 0 |
| 11 | B0_02 | GPIO2 | 2 |
| 12 | B0_01 | GPIO2 | 1 |
| 24 | AD_B0_12 | GPIO1 | 12 |
| 25 | AD_B0_13 | GPIO1 | 13 |
| 26 | AD_B1_14 | GPIO1 | 30 |
| 27 | AD_B1_15 | GPIO1 | 31 |
| 28 | EMC_32 | GPIO3 | 18 |

Optimized read would be 4 register reads + bitmask extraction instead of 16 individual
pin reads. This is left as a future optimization.

---

## Summary of Recommendations

| # | Finding | Severity | Effort | Action |
|---|---------|----------|--------|--------|
| 1 | /OE /CE clamp diodes | Critical | Low | Route through spare 245 channels |
| 2 | led_error() hangs | Critical | Low | Replace with timed blink + fallthrough |
| 3 | Data bus unprotected | Critical | Medium | Add 3rd 74HCT245 (PCB) or series R (breadboard) |
| 4 | Slow ISR GPIO reads | Recommended | Medium | Direct port register reads |
| 5 | String heap fragmentation | Recommended | Low | Fixed char buffer |
| 6 | toInt() silent zero | Recommended | Low | Validate numeric input |
| 7 | File handle leak | Recommended | Low | Use `with` statement |
| 8 | No bulk decoupling | Recommended | Low | Add 10µF cap |
| 9 | volatile cast-away | Recommended | Low | Temp buffer or barrier |
| 10 | BOM package mismatch | Nice-to-have | Low | Clarify in docs |
| 11 | Pin 27 pull-up | Nice-to-have | Low | Add 10kΩ pull-up |
| 12 | KiCad symbols | Nice-to-have | High | Proper schematic |
| 13 | No watchdog | Nice-to-have | Low | Enable HW watchdog |
| 14 | Python version pinned | Nice-to-have | Low | Use "3.x" |
