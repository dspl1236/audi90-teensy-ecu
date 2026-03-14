# Credits & Acknowledgements

## 034 Motorsport — RIP Chip Tool

The map address tables, display formulas, scramble algorithm, and checksum
logic implemented in this project were derived from **034 Motorsport's
RIP Chip tool** for Hitachi ECUs.

034 Motorsport produced the RIP Chip hardware and the accompanying ECUGUI
software for reading and editing Hitachi ECU EPROMs used in 7A and other
Audi/VW engines of the era. They no longer actively sell or support the RIP
Chip for these older ECUs, but their tool was invaluable to the 7A community
and the knowledge encoded in it is what makes this project possible.

**What we used from their work:**

- The `.034` file format — every byte is bit-scrambled using a specific
  transform (`algOne`) before being written to disk. We decompiled the
  `ECUGUI.jar` / `CustomizedStore.jar` bundled inside
  `034EFI_Rip_Chip_1_0_0_Hitachi.msi` to understand and replicate this
  algorithm so that stock `.034` files can be loaded into this tool directly.

- The `.ecu` definition files — `7A_Late_Generic_1.01.ecu` and
  `7A_Early_Generic_1.06.ecu` are Java-serialised objects that contain every
  confirmed map address, axis address, display formula, and checksum parameter
  for the 893 906 266 D (7A Late) and 893 906 266 B (7A Early) ECUs
  respectively. These were deserialised and cross-referenced against live ROM
  data to build the address tables in `ecu_profiles.py`.

- The checksum algorithm — `Checksum.applyOldStyle()` from their decompiled
  source describes a byte-sum scheme over the full 32 KB ROM with a correction
  region. We implemented and verified this independently against both stock ROMs.

**This project is not affiliated with or endorsed by 034 Motorsport.**
It is a personal, non-commercial project. If you are working on a newer VAG
platform, 034 still makes excellent hardware worth checking out:

034 Motorsport: https://034motorsport.com

---

## Who This Tool Is For

034 has moved on to newer platforms. The old Hitachi ECUs are largely on
their own now. This tool exists to fill that gap.

If you are keeping a 7A-engined car alive — stock, modified, turbocharged,
stroked, whatever — and you need to adjust the map in your ECU, this is for
you. The workflow is intentionally simple:

1. Read your original EPROM chip with a programmer (T48 / TL866 / similar)
2. Load the `.bin` or `.034` file into this editor
3. Make your changes — fuel map, timing, injector scaler, knock map
4. Save As a new `.bin` (checksum is corrected automatically)
5. Burn the `.bin` back to a fresh 27C512 EPROM, or use the Teensy emulator

**If you can write an EPROM, you can tune your own ECU.** No proprietary
hardware subscription, no discontinued tool, no waiting. Just a chip programmer
and this software.

---

## Hitachi / Bosch Motronic 2.x ECU Community

General knowledge of the Motronic 2.x platform, ECU pinouts, and sensor
scaling factors was gathered from years of community documentation across
various Audi and VW forums. No single source is cited here because the
collective knowledge is too distributed — but it is appreciated.

---

## Tools Used

| Tool | Purpose |
|------|---------|
| [Procyon Decompiler](https://github.com/ststeiger/procyon) | Java `.class` → source for ECUGUI analysis |
| [T48 / TL866-3G](https://www.xgecu.com) | Physical EPROM reading & writing (27C512) |
| [Teensy 4.1](https://www.pjrc.com/store/teensy41.html) | EPROM emulator microcontroller |
| [PlatformIO](https://platformio.org) | Firmware build system |
| [PyQt5](https://pypi.org/project/PyQt5/) | Tuner application GUI |

---

## Development

This project was developed by **dspl1236** in collaboration with
**Claude** (Anthropic) — firmware architecture, ROM analysis, CI pipeline,
tuner application, and the companion HachiROM library were all built across
a series of sessions using Claude as a development partner.

---

## Project Context

Built for a **1990 Audi 90 with a stroked 2.6 L 7A 20v engine**
(AAF 95.6 mm crank, VW CCTA rods and pistons, dual 7A head gaskets).
The Teensy 4.1 emulates a 27C512 EPROM electrically, allowing live ROM
swaps from an SD card without removing the ECU.

ECU: **893 906 266 D** (7A Late, 4-connector)
