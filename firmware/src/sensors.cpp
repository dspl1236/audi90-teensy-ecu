// =============================================================================
// sensors.cpp — MAP, knock, TPS, IAT, RPM
//
// GM 3-bar MAP sensor #55567257
//   Supply: 5V, Output: 0.5–4.5V linear → 10–304 kPa absolute
//   Voltage divider: 10kΩ/20kΩ scales 4.5V → 3.0V for Teensy ADC
//   ADC reference: 3.3V internal
//
// Bosch knock sensor 0261231006
//   Piezo accelerometer, AC output centred at 0V
//   Bias circuit: 100kΩ+100kΩ voltage divider from 3.3V → 1.65V DC bias
//   Teensy ADC reads bias + AC signal, software detects peaks above threshold
//
// TPS — stock NTC potentiometer, tee'd off at ECU connector
//   10kΩ/20kΩ divider scales 4.5V → 3.0V
//
// IAT — stock NTC thermistor (negative temp coefficient)
//   Teensy measures resistance via fixed pullup, converts via lookup table
//
// RPM — coil/ignition pulse on PIN_RPM
//   Interrupt-based period measurement → RPM calculation
//   7A engine: 4-cylinder, wasted spark = 2 pulses per revolution
// =============================================================================

#include "sensors.h"
#include "config.h"

// ---------------------------------------------------------------------------
// ADC configuration
// Teensy 4.1: 12-bit ADC (0–4095 = 0–3.3V)
// ---------------------------------------------------------------------------
#define ADC_RESOLUTION    12
#define ADC_MAX           4095.0f
#define ADC_VREF          3.3f

// Oversampling: average N readings to reduce noise
#define ADC_OVERSAMPLE    8

// ---------------------------------------------------------------------------
// Sensor state
// ---------------------------------------------------------------------------
static float map_kPa    = 101.3f;   // Start at atmospheric
static float tps_pct    = 0.0f;
static float iat_C      = 20.0f;
static float knock_V    = 0.0f;
static bool  knocking   = false;

// RPM measurement via interrupt
static volatile uint32_t lastPulseUs  = 0;
static volatile uint32_t pulseInterval = 0;   // µs between pulses
static volatile bool     newPulse     = false;
static int               rpm          = 0;

// Knock peak detection window
#define KNOCK_WINDOW_MS   20    // Sample window for peak detection
static uint32_t knockWindowStart = 0;
static float    knockPeakV       = 0.0f;

// ---------------------------------------------------------------------------
// IAT lookup table — NTC thermistor resistance vs temperature
// Stock Bosch NTC: R25 = 2252Ω, B = 3988K
// Pullup resistor: 2.2kΩ to 3.3V
// Values: {ADC count, temperature °C}
// ---------------------------------------------------------------------------
static const int16_t iatTable[][2] = {
  {  200, 120 },
  {  280, 100 },
  {  400,  80 },
  {  580,  60 },
  {  820,  40 },
  { 1100,  25 },
  { 1450,  10 },
  { 1800,   0 },
  { 2150, -10 },
  { 2500, -20 },
  { 2800, -30 },
  { 3072, -40 },
};
static const int iatTableSize = sizeof(iatTable) / sizeof(iatTable[0]);

// ---------------------------------------------------------------------------
// adcToVolts() — convert raw ADC count to voltage at Teensy pin
// ---------------------------------------------------------------------------
static inline float adcToVolts(int raw) {
  return (raw / ADC_MAX) * ADC_VREF;
}

// ---------------------------------------------------------------------------
// readADCAvg() — oversampled ADC read
// ---------------------------------------------------------------------------
static int readADCAvg(uint8_t pin) {
  uint32_t sum = 0;
  for (int i = 0; i < ADC_OVERSAMPLE; i++) {
    sum += analogRead(pin);
  }
  return (int)(sum / ADC_OVERSAMPLE);
}

// ---------------------------------------------------------------------------
// updateMAP()
// GM 3-bar MAP: V_out = 0.5 + (kPa - 10) * (4.0 / 294.0)
// Inverted: kPa = ((V_out - 0.5) / 4.0) * 294.0 + 10
//
// But V_out is divided by 10k/20k before Teensy ADC:
//   V_teensy = V_out * (20k / 30k) = V_out * 0.6667
//   V_out    = V_teensy / 0.6667
// ---------------------------------------------------------------------------
static void updateMAP() {
  int raw = readADCAvg(PIN_MAP);
  float v_teensy = adcToVolts(raw);
  float v_sensor = v_teensy / 0.6667f;   // Undo voltage divider

  // Clamp to sensor output range
  v_sensor = constrain(v_sensor, MAP_V_MIN, MAP_V_MAX);

  // Convert to kPa
  map_kPa = ((v_sensor - 0.5f) / 4.0f) * 294.0f + 10.0f;
  map_kPa = constrain(map_kPa, MAP_KPA_MIN, MAP_KPA_MAX);
}

// ---------------------------------------------------------------------------
// updateTPS()
// Linear 0.5–4.5V → 0–100%
// Same voltage divider as MAP (10k/20k)
// ---------------------------------------------------------------------------
static void updateTPS() {
  int raw = readADCAvg(PIN_TPS);
  float v_teensy = adcToVolts(raw);
  float v_sensor = v_teensy / 0.6667f;

  v_sensor = constrain(v_sensor, TPS_V_MIN, TPS_V_MAX);
  tps_pct = ((v_sensor - TPS_V_MIN) / (TPS_V_MAX - TPS_V_MIN)) * 100.0f;
  tps_pct = constrain(tps_pct, 0.0f, 100.0f);
}

// ---------------------------------------------------------------------------
// updateIAT()
// NTC thermistor: interpolate from lookup table
// ---------------------------------------------------------------------------
static void updateIAT() {
  int raw = readADCAvg(PIN_IAT);

  // Clamp to table bounds
  if (raw <= iatTable[0][0]) {
    iat_C = (float)iatTable[0][1];
    return;
  }
  if (raw >= iatTable[iatTableSize - 1][0]) {
    iat_C = (float)iatTable[iatTableSize - 1][1];
    return;
  }

  // Linear interpolation between table entries
  for (int i = 0; i < iatTableSize - 1; i++) {
    if (raw >= iatTable[i][0] && raw < iatTable[i + 1][0]) {
      float frac = (float)(raw - iatTable[i][0]) /
                   (float)(iatTable[i + 1][0] - iatTable[i][0]);
      iat_C = iatTable[i][1] + frac * (iatTable[i + 1][1] - iatTable[i][1]);
      return;
    }
  }
}

// ---------------------------------------------------------------------------
// updateKnock()
// Peak detection over KNOCK_WINDOW_MS window
// Bias is 1.65V (Vref/2). Signal swings above/below.
// We measure AC amplitude = |V_reading - V_bias|
// ---------------------------------------------------------------------------
static void updateKnock() {
  int raw = readADCAvg(PIN_KNOCK);
  float v = adcToVolts(raw);
  float amplitude = fabsf(v - KNOCK_BIAS_V);

  // Track peak within window
  if (amplitude > knockPeakV) {
    knockPeakV = amplitude;
  }

  // Evaluate window every KNOCK_WINDOW_MS
  if (millis() - knockWindowStart >= KNOCK_WINDOW_MS) {
    knock_V  = knockPeakV;
    knocking = (knockPeakV > KNOCK_THRESHOLD_V);
    knockPeakV       = 0.0f;
    knockWindowStart = millis();

#ifdef DEBUG_SENSORS
    if (knocking) {
      Serial.print(F("KNOCK peak: "));
      Serial.print(knock_V, 3);
      Serial.println(F("V"));
    }
#endif
  }
}

// ---------------------------------------------------------------------------
// RPM interrupt service routine
// Triggered on falling edge of ignition pulse (coil negative)
// 4-cylinder wasted spark: 2 pulses per crankshaft revolution
// ---------------------------------------------------------------------------
FASTRUN static void rpm_isr() {
  uint32_t now = micros();
  pulseInterval = now - lastPulseUs;
  lastPulseUs   = now;
  newPulse      = true;
}

// ---------------------------------------------------------------------------
// updateRPM()
// Called from main loop — converts pulse interval to RPM
// Timeout: if no pulse for >2s, RPM = 0 (engine stopped)
// ---------------------------------------------------------------------------
static void updateRPM() {
  if (newPulse) {
    newPulse = false;
    if (pulseInterval > 0) {
      // pulses per minute = 1,000,000µs/interval * 60s
      // RPM = (pulses/min) / 2  (wasted spark = 2 pulses/rev)
      rpm = (int)((60000000UL / pulseInterval) / 2);
      rpm = constrain(rpm, 0, 8000);
    }
  }

  // Stale reading — engine stopped
  if (micros() - lastPulseUs > 2000000UL) {
    rpm = 0;
  }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

void sensors_init() {
  analogReadResolution(ADC_RESOLUTION);
  analogReadAveraging(1);   // We do our own oversampling

  pinMode(PIN_MAP,   INPUT);
  pinMode(PIN_TPS,   INPUT);
  pinMode(PIN_IAT,   INPUT);
  pinMode(PIN_KNOCK, INPUT);
  pinMode(PIN_RPM,   INPUT_PULLUP);

  // RPM interrupt — falling edge of ignition pulse
  attachInterrupt(digitalPinToInterrupt(PIN_RPM), rpm_isr, FALLING);

  knockWindowStart = millis();

  Serial.println(F("  Sensors: MAP, TPS, IAT, knock, RPM initialized"));
  Serial.print(F("  Knock threshold: "));
  Serial.print(KNOCK_THRESHOLD_V, 2);
  Serial.println(F("V above bias"));
}

void sensors_update() {
  updateMAP();
  updateTPS();
  updateIAT();
  updateKnock();
  updateRPM();
}

float sensors_getMAP_kPa()   { return map_kPa; }
float sensors_getTPS_pct()   { return tps_pct; }
float sensors_getIAT_C()     { return iat_C; }
float sensors_getKnock_V()   { return knock_V; }
bool  sensors_isKnocking()   { return knocking; }
int   sensors_getRPM()       { return rpm; }
