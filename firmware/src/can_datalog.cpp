// =============================================================================
// datalog.cpp — SD card CSV datalogger
//
// Logs at DATALOG_INTERVAL_MS (default 100ms = 10Hz) to DATALOG_FILENAME.
// CSV format:
//   millis, rpm, map_kpa, tps_pct, iat_c, afr, fuel_trim, knock_v, knock_retard
//
// SD card on Teensy 4.1 built-in SDCARD slot (BUILTIN_SDCARD).
// File is left open between writes for speed — flushed every 10 rows.
// =============================================================================

#include "datalog.h"
#include "config.h"
#include "sensors.h"
#include "wideband.h"
#include "corrections.h"
#include <SD.h>

static File     logFile;
static bool     logReady   = false;
static uint32_t lastLogMs  = 0;
static uint32_t rowCount   = 0;
#define FLUSH_EVERY_N_ROWS  10

// Generate a unique filename based on millis to avoid overwriting
static char logFilename[32];

void datalog_init() {
  // SD already initialized in main.cpp — just open the log file
  snprintf(logFilename, sizeof(logFilename), "log_%08lu.csv", millis());

  logFile = SD.open(logFilename, FILE_WRITE);
  if (!logFile) {
    Serial.println(F("  Datalog: failed to open log file"));
    return;
  }

  // Write CSV header
  logFile.println(F("millis,rpm,map_kpa,tps_pct,iat_c,afr,fuel_trim_pct,knock_v,knock_retard_deg"));
  logFile.flush();
  logReady = true;

  Serial.print(F("  Datalog: logging to "));
  Serial.println(logFilename);
}

void datalog_update() {
  if (!logReady) return;
  if (millis() - lastLogMs < DATALOG_INTERVAL_MS) return;
  lastLogMs = millis();

  logFile.print(millis());                              logFile.print(',');
  logFile.print(sensors_getRPM());                      logFile.print(',');
  logFile.print(sensors_getMAP_kPa(), 1);               logFile.print(',');
  logFile.print(sensors_getTPS_pct(), 1);               logFile.print(',');
  logFile.print(sensors_getIAT_C(), 1);                 logFile.print(',');
  logFile.print(wideband_getAFR(), 2);                  logFile.print(',');
  logFile.print(corrections_getFuelTrim() * 100.0f, 1); logFile.print(',');
  logFile.print(sensors_getKnock_V(), 3);               logFile.print(',');
  logFile.println(corrections_getKnockRetard(), 1);

  rowCount++;
  if (rowCount % FLUSH_EVERY_N_ROWS == 0) {
    logFile.flush();
  }
}


// =============================================================================
// can_bus.cpp — TJA1051 CAN transceivers via FlexCAN_T4
//
// CAN1 (pins 22/23): OBD2 port — broadcast sensor data at 10Hz
// CAN2 (pins 1/0):   Gauge cluster / external datalogger
//
// Frame format (CAN_ID_SENSORS = 0x100), 8 bytes:
//   Bytes 0–1: RPM (uint16, big-endian)
//   Bytes 2–3: MAP kPa * 10 (uint16)
//   Byte  4:   TPS % (uint8, 0–100)
//   Byte  5:   IAT °C + 40 offset (uint8, allows -40 to +215°C)
//   Bytes 6–7: AFR * 10 (uint16)
// =============================================================================

#include "can_bus.h"
#include "config.h"
#include "sensors.h"
#include "wideband.h"
#include "corrections.h"
#include <FlexCAN_T4.h>

static FlexCAN_T4<CAN1, RX_SIZE_256, TX_SIZE_16> can1;
static FlexCAN_T4<CAN2, RX_SIZE_256, TX_SIZE_16> can2;
static bool canReady = false;
static uint32_t lastCanMs = 0;

void can_init() {
  can1.begin();
  can1.setBaudRate(CAN_BAUD);
  can2.begin();
  can2.setBaudRate(CAN_BAUD);
  canReady = true;
  Serial.println(F("  CAN: CAN1 + CAN2 initialized at 500kbps"));
}

void can_update() {
  if (!canReady) return;
  if (millis() - lastCanMs < 100) return;   // 10Hz broadcast
  lastCanMs = millis();

  CAN_message_t msg;
  msg.id  = CAN_ID_SENSORS;
  msg.len = 8;

  int   rpm_val = sensors_getRPM();
  float map_val = sensors_getMAP_kPa();
  float tps_val = sensors_getTPS_pct();
  float iat_val = sensors_getIAT_C();
  float afr_val = wideband_getAFR();

  uint16_t rpm_enc = (uint16_t)constrain(rpm_val, 0, 65535);
  uint16_t map_enc = (uint16_t)constrain(map_val * 10.0f, 0, 65535);
  uint8_t  tps_enc = (uint8_t)constrain(tps_val, 0, 100);
  uint8_t  iat_enc = (uint8_t)constrain(iat_val + 40.0f, 0, 255);
  uint16_t afr_enc = (uint16_t)constrain(afr_val * 10.0f, 0, 65535);

  msg.buf[0] = (rpm_enc >> 8) & 0xFF;
  msg.buf[1] =  rpm_enc       & 0xFF;
  msg.buf[2] = (map_enc >> 8) & 0xFF;
  msg.buf[3] =  map_enc       & 0xFF;
  msg.buf[4] =  tps_enc;
  msg.buf[5] =  iat_enc;
  msg.buf[6] = (afr_enc >> 8) & 0xFF;
  msg.buf[7] =  afr_enc       & 0xFF;

  can1.write(msg);
  can2.write(msg);
}
