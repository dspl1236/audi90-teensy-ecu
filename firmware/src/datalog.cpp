// =============================================================================
// datalog.cpp — SD card CSV datalogger
//
// Logs at DATALOG_INTERVAL_MS (default 100ms = 10Hz) to a timestamped file.
// CSV format:
//   millis, rpm, map_kpa, tps_pct, iat_c, afr, fuel_trim_pct, knock_v, knock_retard_deg
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
static bool     logReady  = false;
static uint32_t lastLogMs = 0;
static uint32_t rowCount  = 0;
#define FLUSH_EVERY_N_ROWS  10

static char logFilename[32];

void datalog_init() {
  snprintf(logFilename, sizeof(logFilename), "log_%08lu.csv", millis());

  logFile = SD.open(logFilename, FILE_WRITE);
  if (!logFile) {
    Serial.println(F("  Datalog: failed to open log file"));
    return;
  }

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
