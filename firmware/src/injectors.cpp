// =============================================================================
// injectors.cpp — IRLZ44N MOSFET injector pulse width intercept
//
// Strategy:
//   ECU drives injector signal (active LOW pulse = injector open).
//   Teensy monitors each injector signal via interrupt, measures pulse width,
//   then replicates it to the injector with trim applied via MOSFET gate.
//
// Trim:
//   Positive trim = extend pulse (add fuel) = hold gate open longer
//   Negative trim = shorten pulse (remove fuel) = close gate sooner
//   Limit: ±INJ_TRIM_MAX (default ±15%)
//
// Wiring:
//   ECU injector OUT → Teensy INJ_SENSEx pin (monitor)
//                    → MOSFET Source
//   Teensy INJ_OUTx  → MOSFET Gate
//   MOSFET Drain     → Injector +12V side
//   1N4007 flyback   → across injector solenoid
//
// NOTE: For safety, if Teensy loses power or crashes, the MOSFET gate
//   pulls LOW via 10kΩ resistor to GND — injectors stay CLOSED (fail safe).
//   IRLZ44N is logic-level, fully on at 3.3V gate drive.
//
// Phase 1: Simple pass-through with trim applied in software
// Phase 2: Hardware timer intercept for microsecond precision
// =============================================================================

#include "injectors.h"
#include "config.h"

// ---------------------------------------------------------------------------
// Per-injector state
// ---------------------------------------------------------------------------
struct InjectorState {
  uint8_t  sensePin;         // Monitor ECU output
  uint8_t  gatePin;          // Drive MOSFET gate
  volatile uint32_t pulseStartUs;
  volatile uint32_t pulseWidthUs;
  volatile bool     active;
};

static InjectorState inj[4] = {
  { .sensePin = 24, .gatePin = PIN_INJ0 },
  { .sensePin = 25, .gatePin = PIN_INJ1 },
  { .sensePin = 26, .gatePin = PIN_INJ2 },
  { .sensePin = 27, .gatePin = PIN_INJ3 },
};
// NOTE: Sense pins 24–27 are placeholders — assign based on ECU wiring harness.
// These are separate from gate pins and tap the ECU injector output signal.

static float trimFraction = 0.0f;   // Set by corrections module
static bool  interceptEnabled = false;

// ---------------------------------------------------------------------------
// ISR helpers — one per injector
// Called on both edges of ECU injector signal (active LOW)
// ---------------------------------------------------------------------------
FASTRUN static void inj_isr(InjectorState* s) {
  if (!interceptEnabled) {
    // Pass-through: mirror ECU signal directly to gate
    bool ecuSignal = !digitalReadFast(s->sensePin);  // Active LOW → invert
    digitalWriteFast(s->gatePin, ecuSignal);
    return;
  }

  bool falling = !digitalReadFast(s->sensePin);  // LOW = pulse start

  if (falling) {
    // Pulse start — open gate immediately
    s->pulseStartUs = micros();
    s->active       = true;
    digitalWriteFast(s->gatePin, HIGH);
  } else {
    // Pulse end from ECU — apply trim, schedule delayed gate close
    if (s->active) {
      uint32_t ecuWidth = micros() - s->pulseStartUs;
      float newWidth = ecuWidth * (1.0f + trimFraction);
      newWidth = constrain(newWidth,
                           ecuWidth * (1.0f - INJ_TRIM_MAX),
                           ecuWidth * (1.0f + INJ_TRIM_MAX));

      // For shortening: close gate now (trim < 0)
      // For extending: hold gate open (delayMicroseconds in loop handles this)
      if (trimFraction <= 0.0f) {
        digitalWriteFast(s->gatePin, LOW);
        s->active = false;
      }
      // If positive trim, gate stays open — injectors_update() closes it
      s->pulseWidthUs = (uint32_t)newWidth;
    }
  }
}

// Individual ISR wrappers (can't pass args to attachInterrupt directly)
FASTRUN static void isr_inj0() { inj_isr(&inj[0]); }
FASTRUN static void isr_inj1() { inj_isr(&inj[1]); }
FASTRUN static void isr_inj2() { inj_isr(&inj[2]); }
FASTRUN static void isr_inj3() { inj_isr(&inj[3]); }

// ---------------------------------------------------------------------------
// injectors_init()
// ---------------------------------------------------------------------------
void injectors_init() {
  for (int i = 0; i < 4; i++) {
    pinMode(inj[i].gatePin,  OUTPUT);
    pinMode(inj[i].sensePin, INPUT_PULLUP);
    digitalWriteFast(inj[i].gatePin, LOW);   // Start closed
    inj[i].active = false;
  }

  attachInterrupt(digitalPinToInterrupt(inj[0].sensePin), isr_inj0, CHANGE);
  attachInterrupt(digitalPinToInterrupt(inj[1].sensePin), isr_inj1, CHANGE);
  attachInterrupt(digitalPinToInterrupt(inj[2].sensePin), isr_inj2, CHANGE);
  attachInterrupt(digitalPinToInterrupt(inj[3].sensePin), isr_inj3, CHANGE);

  Serial.println(F("  Injectors: MOSFET intercept initialized (pass-through mode)"));
  Serial.println(F("  NOTE: Verify sense pins 24-27 match ECU harness before enabling"));
}

// ---------------------------------------------------------------------------
// injectors_update()
// Called from main loop — handles positive trim gate extension
// ---------------------------------------------------------------------------
void injectors_update() {
  if (!interceptEnabled) return;

  uint32_t now = micros();
  for (int i = 0; i < 4; i++) {
    if (inj[i].active && trimFraction > 0.0f) {
      // Gate has been open since ECU pulse start
      // Close it when we've reached the trimmed pulse width
      if (now - inj[i].pulseStartUs >= inj[i].pulseWidthUs) {
        digitalWriteFast(inj[i].gatePin, LOW);
        inj[i].active = false;
      }
    }
  }
}

// ---------------------------------------------------------------------------
// injectors_setTrim()
// Called by corrections module
// ---------------------------------------------------------------------------
void injectors_setTrim(float trim) {
  trimFraction = constrain(trim, -INJ_TRIM_MAX, INJ_TRIM_MAX);

  // Auto-enable intercept once corrections are active
  // Stays disabled until corrections module explicitly sets a trim
  if (!interceptEnabled && fabsf(trimFraction) > 0.001f) {
    interceptEnabled = true;
    Serial.println(F("Injector intercept: ENABLED"));
  }
}
