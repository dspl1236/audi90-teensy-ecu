# TeensyEprom — PCB Design Specification

## Board Concept

A small adapter board that plugs into the ECU's DIP-28 EPROM socket.
The Teensy 4.1 mounts on top via female headers (removable). Two SOIC-20
74HCT245 level shifters and passives sit on the top surface flanking the
Teensy. The DIP-28 pins extend below the board into the ECU socket.

```
                    TOP VIEW
    ┌─────────────────────────────────────────────┐
    │ [C1]  ┌────────────┐                        │
    │       │  U1 SOIC   │   ┌──────────────────┐ │
    │       │  74HCT245  │   │                  │ │
    │       └────────────┘   │  Teensy 4.1      │ │
    │                        │  (in sockets)     │ │
    │  ┌─────────────────┐   │                  │ │
    │  │ DIP-28 pins     │   │                  │ │
    │  │ (through board) │   │                  │ │
    │  └─────────────────┘   │                  │ │
    │                        │                  │ │
    │       ┌────────────┐   │                  │ │
    │       │  U2 SOIC   │   └──────────────────┘ │
    │       │  74HCT245  │                        │
    │ [C2]  └────────────┘  [R1][R2]  [C3] [SW1] │
    └─────────────────────────────────────────────┘

                  BOTTOM VIEW
    ┌─────────────────────────────────────────────┐
    │                                             │
    │           ┌─────────────────┐               │
    │           │  28 DIP pins    │               │
    │           │  extending down │               │
    │           │  into ECU socket│               │
    │           └─────────────────┘               │
    │                                             │
    │   (bottom is otherwise flat — clears        │
    │    ECU daughter board components)            │
    └─────────────────────────────────────────────┘
```

## Board Dimensions

| Parameter | Value | Notes |
|-----------|-------|-------|
| Width | 28 mm | Matches DIP-28 row spacing + margin |
| Length | 65 mm | Teensy 4.1 length + clearance |
| Thickness | 1.6 mm | Standard 2-layer FR4 |
| Layers | 2 | Front copper + back copper |
| Min trace | 0.25 mm | Standard fab capability |
| Min space | 0.2 mm | |
| Min drill | 0.3 mm | Vias |
| Copper weight | 1 oz | |

## Stack Height Budget (from ECU board surface)

| Layer | Height | Running total |
|-------|--------|---------------|
| DIP-28 pin insertion | -3.0 mm | (below surface) |
| PCB | 1.6 mm | 1.6 mm |
| Female header body | 8.5 mm | 10.1 mm |
| Teensy PCB | 1.6 mm | 11.7 mm |
| Teensy top components | 3.0 mm | 14.7 mm |
| **Total above ECU surface** | | **~15 mm** |
| ECU case clearance (est.) | | ~25–30 mm ✓ |

## Component List

| Ref | Part | Package | Value |
|-----|------|---------|-------|
| J1 | DIP-28 turned pin | Through-hole 0.6" | EPROM socket pins |
| U0 | Teensy 4.1 | Via female headers | MCU |
| U1 | 74HCT245 | SOIC-20 (7.5mm body) | Addr low A0–A7 |
| U2 | 74HCT245 | SOIC-20 (7.5mm body) | Addr high A8–A15 |
| R1 | Resistor | 0603 | 1kΩ (/OE clamp) |
| R2 | Resistor | 0603 | 1kΩ (/CE clamp) |
| C1 | Capacitor | 0603 | 100nF (U1 bypass) |
| C2 | Capacitor | 0603 | 100nF (U2 bypass) |
| C3 | Capacitor | 0603 | 100nF (Teensy 3.3V bypass) |
| SW1 | Momentary button | 6mm tactile | Map switch (optional) |

## Netlist

### Power Nets

| Net | Pins |
|-----|------|
| +5V | J1-28(Vcc), Teensy-Vin |
| +3V3 | Teensy-3.3V, U1-20(Vcc), U2-20(Vcc), C1+, C2+, C3+ |
| GND | J1-14, Teensy-GND, U1-1(DIR), U1-10(GND), U1-19(/OE), U2-1(DIR), U2-10(GND), U2-19(/OE), C1−, C2−, C3−, SW1-2 |

### Address Bus — Low Byte (via U1)

| DIP-28 pin | Signal | U1 A-side pin | U1 B-side pin | Teensy pin |
|------------|--------|---------------|---------------|------------|
| 10 | A0 | 2 (A1) | 18 (B1) | 2 |
| 9 | A1 | 3 (A2) | 17 (B2) | 3 |
| 8 | A2 | 4 (A3) | 16 (B3) | 4 |
| 7 | A3 | 5 (A4) | 15 (B4) | 5 |
| 6 | A4 | 6 (A5) | 14 (B5) | 6 |
| 5 | A5 | 7 (A6) | 13 (B6) | 7 |
| 4 | A6 | 8 (A7) | 12 (B7) | 8 |
| 3 | A7 | 9 (A8) | 11 (B8) | 9 |

### Address Bus — High Byte (via U2)

| DIP-28 pin | Signal | U2 A-side pin | U2 B-side pin | Teensy pin |
|------------|--------|---------------|---------------|------------|
| 25 | A8 | 2 (A1) | 18 (B1) | 10 |
| 24 | A9 | 3 (A2) | 17 (B2) | 11 |
| 21 | A10 | 4 (A3) | 16 (B3) | 12 |
| 23 | A11 | 5 (A4) | 15 (B4) | 24 |
| 2 | A12 | 6 (A5) | 14 (B5) | 25 |
| 26 | A13 | 7 (A6) | 13 (B6) | 26 |
| 27 | A14 | 8 (A7) | 12 (B7) | 27 |
| 1 | A15 | 9 (A8) | 11 (B8) | 28 |

### Data Bus — Direct (no buffer)

| DIP-28 pin | Signal | Teensy pin |
|------------|--------|------------|
| 11 | D0 | 14 |
| 12 | D1 | 15 |
| 13 | D2 | 16 |
| 15 | D3 | 17 |
| 16 | D4 | 18 |
| 17 | D5 | 19 |
| 18 | D6 | 20 |
| 19 | D7 | 21 |

### Control Signals

| DIP-28 pin | Signal | Via | Teensy pin |
|------------|--------|-----|------------|
| 22 | /OE | R1 (1kΩ) | 29 |
| 20 | /CE | R2 (1kΩ) | 30 |

### Other

| From | To | Notes |
|------|----|-------|
| Teensy pin 31 | SW1 pin 1 | Button (internal pull-up) |
| SW1 pin 2 | GND | |
| Teensy pin 13 | — | LED (onboard, no PCB trace needed) |
| J1 pin 1 (Vpp/A15) | U2 pin 9 (A8) | A15 routed through U2 |

## Layout Guidelines

### DIP-28 Placement
- Center of board, offset toward one end to leave room for Teensy overhang
- Use turned-pin (machined) DIP-28 through-hole pads — these mate with
  the ECU's existing DIP socket
- Pin 1 orientation: match the EPROM it replaces (notch end)

### Teensy Socket Placement
- Two rows of female headers (1×24 each), 0.6" (15.24mm) apart
- Aligned with DIP-28 along the length axis
- Extends past the DIP-28 footprint by ~30mm
- Pin 1 (GND) at the DIP-28 end of the board

### 245 Placement
- U1 and U2 flanking the Teensy headers, one per side
- SOIC-20 pads parallel to the Teensy long axis
- Place as close to the DIP-28 pins as routing allows (short address traces)

### Routing Priority
1. **GND plane** — pour on back copper layer, maximize coverage
2. **+3V3 rail** — wide trace (0.6mm+) from Teensy 3.3V to U1-20 and U2-20
3. **Address bus** — 8 traces from DIP-28 to each 245 A-side, 8 from B-side to Teensy
4. **Data bus** — 8 direct traces from DIP-28 to Teensy (shortest path)
5. **Control** — /OE and /CE through resistor pads to Teensy

### Ground Fill
- Back copper: full ground pour
- Front copper: ground pour where space permits (around components)
- Via stitch ground pours together

### Silkscreen
- Component refs (U1, U2, R1, R2, C1, C2, C3, SW1)
- Board name: "TeensyEprom v1.0"
- Pin 1 marker on DIP-28
- Teensy orientation arrow
- "github.com/dspl1236/TeensyEprom"

## Fab Notes

- 2-layer, 1.6mm FR4, 1oz copper, HASL lead-free
- Board outline: rounded corners (1mm radius)
- No components on bottom side — must sit flat against ECU daughter board
- Minimum order: JLCPCB 5 boards ~$2, SOIC assembly ~$8 extra

## Assembly Order

1. Solder U1 and U2 (SOIC-20) — use flux, drag solder or hot air
2. Solder R1, R2 (0603)
3. Solder C1, C2, C3 (0603)
4. Solder female headers (2 × 24-pin)
5. Solder DIP-28 turned pins from bottom
6. Solder SW1 (optional)
7. Insert Teensy 4.1 into female headers
8. Flash firmware via USB
9. Plug into ECU EPROM socket
