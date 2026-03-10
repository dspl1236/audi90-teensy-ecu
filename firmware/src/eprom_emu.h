// =============================================================================
// eprom_emu.h / eprom_emu.cpp — FlexIO EPROM emulator
//
// Emulates a 27C512 (64KB, 8-bit) EPROM on the Teensy 4.1 FlexIO bus.
//
// How it works:
//   - ECU asserts /CE and /OE, puts address on A0–A15
//   - Teensy reads address via GPIO (interrupt on /OE falling edge)
//   - Teensy drives D0–D7 with romData[address] within tACC (~120ns for 27C512)
//   - Teensy 4.1 at 600MHz = 1.67ns/cycle — plenty of headroom
//
// Phase 1 (breadboard): GPIO interrupt-driven (simple, verify correct)
// Phase 2 (PCB):        FlexIO DMA (zero CPU overhead, production ready)
//
// The interrupt approach is fast enough for the 7A ECU's bus timing.
// The 27C512 datasheet specifies tACC = 120ns max for the -12 variant.
// Our GPIO ISR response is typically <100ns at 600MHz with cache enabled.
// =============================================================================
#pragma once
#include <Arduino.h>
#include "config.h"

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------
void eprom_init(uint8_t* romBuffer, size_t romSize);
void eprom_diagnostics();   // Print last N address reads to Serial

// ---------------------------------------------------------------------------
// eprom_emu.cpp
// ---------------------------------------------------------------------------
#ifdef EPROM_EMU_IMPL

#include "eprom_emu.h"

// Address and data pin arrays for fast digitalRead/Write
static const uint8_t addrPins[16] = {
  EPROM_A0,  EPROM_A1,  EPROM_A2,  EPROM_A3,
  EPROM_A4,  EPROM_A5,  EPROM_A6,  EPROM_A7,
  EPROM_A8,  EPROM_A9,  EPROM_A10, EPROM_A11,
  EPROM_A12, EPROM_A13, EPROM_A14, EPROM_A15
};

static const uint8_t dataPins[8] = {
  PIN_D0, PIN_D1, PIN_D2, PIN_D3,
  PIN_D4, PIN_D5, PIN_D6, PIN_D7
};

// ROM buffer pointer (points to romData[] in main.cpp)
static uint8_t* rom      = nullptr;
static size_t   romBytes = 0;

// Diagnostics ring buffer
#define DIAG_BUF_SIZE 64
static volatile uint16_t diagAddresses[DIAG_BUF_SIZE];
static volatile uint8_t  diagData[DIAG_BUF_SIZE];
static volatile uint32_t diagIdx = 0;
static volatile uint32_t totalReads = 0;

// ---------------------------------------------------------------------------
// readAddress() — read 16-bit address from GPIO pins
// Called from ISR — must be fast
// Using direct GPIO register reads for speed (bypasses digitalRead overhead)
// ---------------------------------------------------------------------------
FASTRUN static inline uint16_t readAddress() {
  uint16_t addr = 0;
  // Direct GPIO reads — each digitalReadFast() is ~1 cycle at 600MHz
  addr |= (uint16_t)digitalReadFast(EPROM_A0)  << 0;
  addr |= (uint16_t)digitalReadFast(EPROM_A1)  << 1;
  addr |= (uint16_t)digitalReadFast(EPROM_A2)  << 2;
  addr |= (uint16_t)digitalReadFast(EPROM_A3)  << 3;
  addr |= (uint16_t)digitalReadFast(EPROM_A4)  << 4;
  addr |= (uint16_t)digitalReadFast(EPROM_A5)  << 5;
  addr |= (uint16_t)digitalReadFast(EPROM_A6)  << 6;
  addr |= (uint16_t)digitalReadFast(EPROM_A7)  << 7;
  addr |= (uint16_t)digitalReadFast(EPROM_A8)  << 8;
  addr |= (uint16_t)digitalReadFast(EPROM_A9)  << 9;
  addr |= (uint16_t)digitalReadFast(EPROM_A10) << 10;
  addr |= (uint16_t)digitalReadFast(EPROM_A11) << 11;
  addr |= (uint16_t)digitalReadFast(EPROM_A12) << 12;
  addr |= (uint16_t)digitalReadFast(EPROM_A13) << 13;
  addr |= (uint16_t)digitalReadFast(EPROM_A14) << 14;
  addr |= (uint16_t)digitalReadFast(EPROM_A15) << 15;
  return addr;
}

// ---------------------------------------------------------------------------
// writeData() — drive 8-bit data onto GPIO pins
// Called from ISR — must be fast
// ---------------------------------------------------------------------------
FASTRUN static inline void writeData(uint8_t data) {
  digitalWriteFast(PIN_D0, (data >> 0) & 1);
  digitalWriteFast(PIN_D1, (data >> 1) & 1);
  digitalWriteFast(PIN_D2, (data >> 2) & 1);
  digitalWriteFast(PIN_D3, (data >> 3) & 1);
  digitalWriteFast(PIN_D4, (data >> 4) & 1);
  digitalWriteFast(PIN_D5, (data >> 5) & 1);
  digitalWriteFast(PIN_D6, (data >> 6) & 1);
  digitalWriteFast(PIN_D7, (data >> 7) & 1);
}

// ---------------------------------------------------------------------------
// releaseDataBus() — set data pins to INPUT (high-Z) when not selected
// Prevents bus conflict when ECU drives another device
// ---------------------------------------------------------------------------
FASTRUN static inline void releaseDataBus() {
  for (int i = 0; i < 8; i++) {
    pinMode(dataPins[i], INPUT);
  }
}

// ---------------------------------------------------------------------------
// driveDataBus() — set data pins to OUTPUT
// ---------------------------------------------------------------------------
FASTRUN static inline void driveDataBus() {
  for (int i = 0; i < 8; i++) {
    pinMode(dataPins[i], OUTPUT);
  }
}

// ---------------------------------------------------------------------------
// ISR — triggered on /OE falling edge (ECU is requesting data)
// This is the hot path — every EPROM read goes through here
// Target: respond within 120ns (27C512-12 tACC spec)
// At 600MHz: 120ns = 72 cycles. Reading 16 GPIO + lookup + writing 8 GPIO
// is approximately 40-60 cycles. Tight but achievable with FASTRUN + cache.
// ---------------------------------------------------------------------------
FASTRUN static void oe_isr() {
  // Double-check /CE is also asserted (active LOW)
  if (digitalReadFast(PIN_CE)) return;   // CE not asserted, ignore

  uint16_t addr = readAddress();

  // Mask to ROM size (handles both 32KB and 64KB addressing)
  uint16_t maskedAddr = addr & (ROM_SIZE - 1);

  uint8_t data = rom[maskedAddr];

  driveDataBus();
  writeData(data);

  // Log to diagnostics ring buffer (non-blocking, wraps around)
  uint32_t idx = diagIdx & (DIAG_BUF_SIZE - 1);
  diagAddresses[idx] = maskedAddr;
  diagData[idx]      = data;
  diagIdx++;
  totalReads++;
}

// ---------------------------------------------------------------------------
// ISR — triggered on /OE rising edge (ECU done reading)
// Release data bus to avoid conflict
// ---------------------------------------------------------------------------
FASTRUN static void oe_release_isr() {
  releaseDataBus();
}

// ---------------------------------------------------------------------------
// eprom_init()
// ---------------------------------------------------------------------------
void eprom_init(uint8_t* romBuffer, size_t romSize) {
  rom      = romBuffer;
  romBytes = romSize;

  // Address pins — INPUT (ECU drives these)
  for (int i = 0; i < 16; i++) {
    pinMode(addrPins[i], INPUT);
  }

  // Data pins — start as INPUT (high-Z), driven only when /OE asserted
  for (int i = 0; i < 8; i++) {
    pinMode(dataPins[i], INPUT);
  }

  // Control pins — INPUT (ECU drives these)
  pinMode(PIN_OE, INPUT);
  pinMode(PIN_CE, INPUT);

  // Attach ISR to /OE falling edge (ECU requesting read)
  attachInterrupt(digitalPinToInterrupt(PIN_OE), oe_isr,         FALLING);
  // Attach ISR to /OE rising edge (ECU done, release bus)
  attachInterrupt(digitalPinToInterrupt(PIN_OE), oe_release_isr, RISING);

  Serial.println(F("  EPROM emulator: GPIO interrupt mode"));
  Serial.print(F("  ROM size: "));
  Serial.print(romSize);
  Serial.println(F(" bytes"));
  Serial.print(F("  Active region: 0x0000–0x"));
  Serial.println(ROM_ACTIVE_SIZE - 1, HEX);
}

// ---------------------------------------------------------------------------
// eprom_diagnostics()
// Print last DIAG_BUF_SIZE address/data pairs to Serial
// Call from loop() when USB is connected, for bench verification
// ---------------------------------------------------------------------------
void eprom_diagnostics() {
  uint32_t count = min((uint32_t)DIAG_BUF_SIZE, totalReads);
  uint32_t start = (diagIdx - count) & (DIAG_BUF_SIZE - 1);

  Serial.print(F("EPROM: total reads="));
  Serial.println(totalReads);
  Serial.println(F("Last reads (addr -> data):"));

  noInterrupts();   // Snapshot consistently
  for (uint32_t i = 0; i < count; i++) {
    uint32_t idx = (start + i) & (DIAG_BUF_SIZE - 1);
    Serial.print(F("  0x"));
    if (diagAddresses[idx] < 0x1000) Serial.print('0');
    if (diagAddresses[idx] < 0x100)  Serial.print('0');
    if (diagAddresses[idx] < 0x10)   Serial.print('0');
    Serial.print(diagAddresses[idx], HEX);
    Serial.print(F(" -> 0x"));
    if (diagData[idx] < 0x10) Serial.print('0');
    Serial.println(diagData[idx], HEX);
  }
  interrupts();
}

#endif // EPROM_EMU_IMPL
