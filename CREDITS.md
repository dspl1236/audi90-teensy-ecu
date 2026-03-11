# Credits & Acknowledgements

## 034 Motorsport — RIP Chip Tool

The map address tables, display formulas, scramble algorithm, and checksum
logic implemented in this project were derived from **034 Motorsport's
RIP Chip tool** for Hitachi ECUs.

034 Motorsport produces and sells the RIP Chip hardware and the accompanying
ECUGUI software for reading and writing Hitachi ECU EPROMs used in 7A and
other Audi/VW engines of the era.

**What we used from their work:**

- The `.034` file format — every byte is bit-scrambled using a specific
  transform (`algOne`) before being written to disk. We decompiled the
  `ECUGUI.jar` / `CustomizedStore.jar` bundled inside
  `034EFI_Rip_Chip_1_0_0_Hitachi.msi` to understand and replicate this
  algorithm so that stock `.034` files could be loaded into this tool.

- The `.ecu` definition files — `7A_Late_Generic_1.01.ecu` and
  `7A_Early_Generic_1.06.ecu` are Java-serialised objects that contain every
  confirmed map address, axis address, display formula, and checksum parameter
  for the 893 906 266 D (7A Late) and 893 906 266 B (7A Early) ECUs
  respectively. These were deserialised and cross-referenced against live ROM
  data to build the address tables in `ecu_profiles.py`.

- The checksum algorithm — `Checksum.applyOldStyle()` from their decompiled
  source describes a byte-sum scheme over the full 32 KB ROM with a correction
  region. We implemented and verified this independently against both stock
  ROMs.

**This project is not affiliated with or endorsed by 034 Motorsport.**
It is a personal, non-commercial project built around a Teensy 4.1 EPROM
emulator for a single engine build. If you are tuning a 7A-engined car and
want a polished, supported tool, buy their RIP Chip — it is the real deal and
the research that made this project possible came directly from their work.

034 Motorsport: https://store.034motorsport.com

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

## Project Context

This tool was built for a **1990 Audi 90 with a stroked 2.6 L 7A 20v engine**
(AAF 95.6 mm crank, VW CCTA rods and pistons, dual 7A head gaskets).
The Teensy 4.1 emulates a 27C512 EPROM electrically, allowing live ROM swaps
from an SD card without pulling the ECU from the car.

ECU: **893 906 266 D** (7A Late, 4-connector)
