// =============================================================================
// config.h — Pin assignments and compile-time constants
// Teensy 4.1
// =============================================================================
#pragma once

// ---------------------------------------------------------------------------
// ROM
// ---------------------------------------------------------------------------
#define ROM_SIZE              65536
#define ROM_ACTIVE_SIZE       32768
#define ROM_FILENAME          "tune.bin"
#define ROM_FALLBACK_FILENAME "stock.bin"

// ---------------------------------------------------------------------------
// EPROM emulator — FlexIO address/data bus
// Prefixed EPROM_ to avoid conflicts with Teensyduino PIN_A0-PIN_A15 defines.
// ---------------------------------------------------------------------------
#define EPROM_A0    19
#define EPROM_A1    18
#define EPROM_A2    17
#define EPROM_A3    16
#define EPROM_A4    15
#define EPROM_A5    14
#define EPROM_A6    40
#define EPROM_A7    41
#define EPROM_A8    42
#define EPROM_A9    43
#define EPROM_A10   44
#define EPROM_A11   45
#define EPROM_A12   6
#define EPROM_A13   9
#define EPROM_A14   32
#define EPROM_A15   8

#define EPROM_D0    2
#define EPROM_D1    3
#define EPROM_D2    4
#define EPROM_D3    5
#define EPROM_D4    33
#define EPROM_D5    34
#define EPROM_D6    35
#define EPROM_D7    36

#define EPROM_OE    37
#define EPROM_CE    38

// ---------------------------------------------------------------------------
// Wideband — Spartan 3 Lite OEM UART
// ---------------------------------------------------------------------------
#define WIDEBAND_SERIAL   Serial1
#define WIDEBAND_BAUD     9600
#define PIN_WB_RX         0
#define AFR_MIN           10.0f
#define AFR_MAX           20.0f
#define AFR_TARGET        14.7f

// ---------------------------------------------------------------------------
// Sensors
// ---------------------------------------------------------------------------
#define PIN_MAP           A1
#define MAP_V_MIN         0.5f
#define MAP_V_MAX         4.5f
#define MAP_KPA_MIN       10.0f
#define MAP_KPA_MAX       304.0f

#define PIN_KNOCK         A2
#define KNOCK_BIAS_V      1.65f
#define KNOCK_THRESHOLD_V 0.3f

#define PIN_TPS           A3
#define TPS_V_MIN         0.5f
#define TPS_V_MAX         4.5f

#define PIN_IAT           A4
#define PIN_RPM           7

// ---------------------------------------------------------------------------
// Injector intercept — IRLZ44N MOSFETs
// ---------------------------------------------------------------------------
#define PIN_INJ0      24
#define PIN_INJ1      25
#define PIN_INJ2      26
#define PIN_INJ3      27
#define INJ_TRIM_MAX  0.15f

// ---------------------------------------------------------------------------
// MAF intercept
// ---------------------------------------------------------------------------
#define MAF_INPUT_FREQUENCY  0
#define MAF_INPUT_ANALOG     1
#define MAF_INPUT_TYPE       MAF_INPUT_FREQUENCY
#define PIN_MAF_IN           20
#define PIN_MAF_OUT          21
#define MAF_DISPLACEMENT_FACTOR  1.130f

// ---------------------------------------------------------------------------
// SD / Datalogger
// ---------------------------------------------------------------------------
#define SD_CS                BUILTIN_SDCARD
#define DATALOG_INTERVAL_MS  100

// ---------------------------------------------------------------------------
// Corrections
// ---------------------------------------------------------------------------
#define FUEL_TRIM_MAX     0.10f
#define FUEL_TRIM_STEP    0.002f
#define KNOCK_RETARD_DEG  2.0f
#define KNOCK_RETARD_MAX  10.0f
#define KNOCK_RECOVER_DEG 0.5f
