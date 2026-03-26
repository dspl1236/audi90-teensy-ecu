// =============================================================================
//  config.h — TeensyEprom pin assignments and configuration
//
//  Teensy 4.1 EPROM emulator — replaces 27C256/27C512 in any parallel-bus ECU.
//
//  Hardware:
//    Teensy 4.1 (IMXRT1062, 600MHz, 3.3V GPIO — NOT 5V tolerant)
//    2x 74HCT245 @ 3.3V Vcc — address bus level shift (ECU 5V -> Teensy 3.3V)
//    Data bus direct — Teensy 3.3V outputs meet ECU TTL VIH (>2.0V)
//    /OE, /CE — 1k series resistors (5V -> 3.3V clamp via Teensy internal diodes)
//
//  Power:
//    EPROM socket pin 28 (Vcc) provides 5V from the ECU.
//    Connect to Teensy Vin pin — the onboard regulator drops to 3.3V.
//    This powers the Teensy, SD card, and both 74HCT245s (from 3.3V rail).
//    No USB power needed once installed — the ECU powers everything.
//    USB can be connected simultaneously for serial commands.
// =============================================================================
#pragma once

// ---------------------------------------------------------------------------
// ROM
// ---------------------------------------------------------------------------
#define ROM_SIZE              65536    // 27C512 = 64KB (full emulated space)
#define ROM_ACTIVE_SIZE       32768    // Most ECUs use 32KB, mirrored to fill 64KB

// ---------------------------------------------------------------------------
// Storage — LittleFS on program flash (primary), SD card (fallback)
//
// Teensy 4.1 has 8MB program flash. Firmware uses ~200KB, leaving plenty
// for a LittleFS partition. 16 ROMs x 32KB = 512KB fits easily in 1MB.
//
// Boot order:
//   1. Try LittleFS — if /maps/ has .bin files, use them
//   2. Fall back to SD card — if LittleFS empty, check SD
//   3. Error — no ROM files found, blink error LED
//
// USB upload writes to LittleFS. SD card is read-only fallback.
// ---------------------------------------------------------------------------
#define LITTLEFS_SIZE         (1024 * 1024)   // 1MB LittleFS partition
#define MAP_DIR               "/maps/"         // directory for ROM files (both FS)
#define MAX_MAPS              16
#define MAX_FILENAME          64

// ---------------------------------------------------------------------------
// Address bus — 16 lines via 2x 74HCT245 @ 3.3V (ECU 5V -> Teensy 3.3V)
//
// U1 (low byte):  A-side = DIP A0-A7 (5V) -> B-side = Teensy pins 2-9
// U2 (high byte): A-side = DIP A8-A15 (5V) -> B-side = Teensy pins 10-12, 24-28
// Both: Vcc=3.3V, DIR=LOW (A->B), /OE=LOW (always enabled)
// ---------------------------------------------------------------------------
static const uint8_t ADDR_PINS[16] = {
//  A0  A1  A2  A3  A4  A5  A6  A7
     2,  3,  4,  5,  6,  7,  8,  9,
//  A8  A9 A10 A11 A12 A13 A14 A15
    10, 11, 12, 24, 25, 26, 27, 28
};

// ---------------------------------------------------------------------------
// Data bus — 8 lines direct to DIP-28 (no buffer needed)
// Teensy 3.3V output -> ECU TTL (VIH > 2.0V)
// ---------------------------------------------------------------------------
static const uint8_t DATA_PINS[8] = {
//  D0  D1  D2  D3  D4  D5  D6  D7
    14, 15, 16, 17, 18, 19, 20, 21
};

// ---------------------------------------------------------------------------
// Control signals — 1k series resistors (5V -> 3.3V via internal clamp)
// ---------------------------------------------------------------------------
static const uint8_t PIN_OE = 29;     // /OE (output enable, active LOW)
static const uint8_t PIN_CE = 30;     // /CE (chip enable, active LOW)

// ---------------------------------------------------------------------------
// Map switcher
// ---------------------------------------------------------------------------
static const uint8_t PIN_BUTTON = 31;  // Momentary switch, active LOW (pull-up)
static const uint8_t PIN_LED    = 13;  // Onboard Teensy LED

// ---------------------------------------------------------------------------
// Data bus buffering (v2.1 hardware option)
//
// Set true if U3 74HCT245 is installed on the data bus.
// U3: Vcc=5V, DIR=HIGH (B→A), /OE tied to EPROM /OE
//   → provides 5V output levels, automatic tri-state, input protection
//   → ISR no longer needs to manage data bus direction or busy-wait
//
// Set false (default) for v2.0 breadboard builds with direct data bus.
// ---------------------------------------------------------------------------
#define DATA_BUS_BUFFERED    false

// ---------------------------------------------------------------------------
// Timing
// ---------------------------------------------------------------------------
#define DEBOUNCE_MS          50
#define LED_BLINK_MS         200
#define BUTTON_HOLD_MS       1000     // Hold for previous map
#define ISR_TIMEOUT_US       100      // Busy-wait ceiling (prevents hang if /OE stuck)

// ---------------------------------------------------------------------------
// USB upload protocol
// ---------------------------------------------------------------------------
// Text: LIST, MAP <n>, INFO, DELETE <name>, FORMAT, DUMP
// Upload: UPLOAD <name> <size>\n + <size> raw bytes + 2-byte CRC16-CCITT
// Download: DOWNLOAD <name>\n -> SIZE <n>\n + <n> bytes + CRC16-CCITT
// ---------------------------------------------------------------------------
#define UPLOAD_TIMEOUT_MS    5000
#define SERIAL_BAUD          115200
#define CMD_BUF_SIZE         128       // Fixed serial command buffer

// ---------------------------------------------------------------------------
// Ident
// ---------------------------------------------------------------------------
#define FW_VERSION           "2.0"
#define IDENT_STRING         "TeensyEprom v" FW_VERSION "\n"
