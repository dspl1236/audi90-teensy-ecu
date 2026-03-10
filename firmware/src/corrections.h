#pragma once
#include <Arduino.h>
// corrections.h — closed-loop fuel trim and knock retard

void  corrections_init();
void  corrections_update(uint8_t* romData);
float corrections_getFuelTrim();
float corrections_getKnockRetard();
