/**
 * usb_tune.h / usb_tune.cpp
 * Teensy 4.1 USB Serial handler for the Audi90 Tuner app
 *
 * Protocol matches serial_comm/protocol.py exactly.
 *
 * USAGE in main.cpp:
 *   #include "usb_tune.h"
 *   // in setup(): usb_tune_init();
 *   // in loop():  usb_tune_loop();
 *
 * Requires:
 *   - extern uint8_t romData[65536];       // from eprom_emu.h
 *   - extern volatile float g_afr;         // from wideband.cpp
 *   - extern volatile int   g_rpm;         // from sensors.cpp
 *   - extern volatile float g_map_kpa;
 *   - extern volatile float g_tps_pct;
 *   - extern volatile float g_iat_c;
 *   - extern volatile float g_fuel_trim;   // from corrections.cpp
 *   - extern volatile float g_knock_v;     // from sensors.cpp
 *   - extern volatile float g_knock_retard;// from corrections.cpp
 *   - extern bool           g_corrections_enabled; // from corrections.cpp
 *   - extern float          g_target_afr;  // from corrections.cpp
 *   - extern char           g_rom_file[32];// active ROM filename
 *   - void load_rom_from_sd(const char* filename); // from eprom_emu.h
 *   - void save_tune_to_sd(const char* filename);  // from eprom_emu.h
 */

#pragma once
#include <Arduino.h>
#include <SD.h>

// ── Stream interval ───────────────────────────────────────────────────────
#define USB_STREAM_HZ      10     // Live data frames per second
#define USB_STREAM_INTERVAL_MS  (1000 / USB_STREAM_HZ)

// ── Forward declarations (must match your other modules) ──────────────────
extern uint8_t  romData[65536];
extern volatile float  g_afr;
extern volatile int    g_rpm;
extern volatile float  g_map_kpa;
extern volatile float  g_tps_pct;
extern volatile float  g_iat_c;
extern volatile float  g_fuel_trim;
extern volatile float  g_knock_v;
extern volatile float  g_knock_retard;
extern bool            g_corrections_enabled;
extern float           g_target_afr;
extern char            g_rom_file[32];

void load_rom_from_sd(const char* filename);
void save_tune_to_sd(const char* filename);

// ── ROM address constants (893906266D confirmed) ──────────────────────────
#define FUEL_MAP_ADDR    0x0000   // Primary Fueling  — 18×16 = 288 bytes
#define TIMING_MAP_ADDR  0x0120   // Primary Timing   — 18×16 = 288 bytes (0x0000 + 288)
#define MAP_ROWS         18       // RPM breakpoints
#define MAP_COLS         16       // Load (kPa) breakpoints
#define MAP_SIZE         (MAP_ROWS * MAP_COLS)   // 288 bytes

// ── SD directory listing helper (implement in your sd/file module) ─────────
// Returns comma-separated list of .bin files on SD root into buf.
extern void list_sd_bin_files(char* buf, size_t buflen);

// ═══════════════════════════════════════════════════════════════════════════
//  usb_tune.cpp  (put in a .cpp file or inline here with #ifdef guard)
// ═══════════════════════════════════════════════════════════════════════════

#ifdef USB_TUNE_IMPL

static uint32_t _last_stream_ms = 0;
static char     _cmd_buf[128];
static uint8_t  _cmd_len = 0;

// ── Init ──────────────────────────────────────────────────────────────────
void usb_tune_init() {
    // USB Serial is always available on Teensy 4.1
    // Nothing to do — Serial is the USB CDC port
    Serial.begin(115200);  // baud ignored on USB CDC, but needed for API
    // Send hello
    delay(500);
    Serial.printf("$STATUS,ready,%lu,%s\n", millis(), g_rom_file);
}

// ── Main loop call ────────────────────────────────────────────────────────
void usb_tune_loop() {
    // 1. Stream live data at USB_STREAM_HZ
    uint32_t now = millis();
    if (now - _last_stream_ms >= USB_STREAM_INTERVAL_MS) {
        _last_stream_ms = now;
        _stream_data();
    }

    // 2. Process incoming commands (non-blocking)
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\n' || c == '\r') {
            if (_cmd_len > 0) {
                _cmd_buf[_cmd_len] = '\0';
                _process_command(_cmd_buf);
                _cmd_len = 0;
            }
        } else if (_cmd_len < sizeof(_cmd_buf) - 1) {
            _cmd_buf[_cmd_len++] = c;
        }
    }
}

// ── Live data stream ──────────────────────────────────────────────────────
static void _stream_data() {
    // $DATA,rpm,map_kpa,tps_pct,iat_c,afr,fuel_trim_pct,knock_v,knock_retard
    Serial.printf("$DATA,%d,%.1f,%.1f,%.1f,%.2f,%+.1f,%.3f,%.1f\n",
        g_rpm,
        g_map_kpa,
        g_tps_pct,
        g_iat_c,
        g_afr,
        g_fuel_trim,
        g_knock_v,
        g_knock_retard
    );
}

// ── CRC32 helper (ISO 3309 / Ethernet polynomial) ────────────────────────────
static uint32_t _crc32_update(uint32_t crc, uint8_t byte) {
    crc ^= byte;
    for (int i = 0; i < 8; i++)
        crc = (crc >> 1) ^ (crc & 1 ? 0xEDB88320u : 0u);
    return crc;
}

// ── Read a newline-terminated line with timeout (ms). Returns length or -1. ──
static int _read_line_timeout(char* buf, int maxlen, uint32_t timeout_ms) {
    uint32_t deadline = millis() + timeout_ms;
    int len = 0;
    while (millis() < deadline) {
        while (Serial.available()) {
            char c = Serial.read();
            if (c == '\n' || c == '\r') {
                if (len > 0) { buf[len] = '\0'; return len; }
            } else if (len < maxlen - 1) {
                buf[len++] = c;
            }
        }
        delayMicroseconds(100);
    }
    return -1;
}

// ── Read exactly 'count' raw bytes with timeout. Returns bytes read. ─────────
static int _read_bytes_timeout(uint8_t* buf, int count, uint32_t timeout_ms) {
    uint32_t deadline = millis() + timeout_ms;
    int got = 0;
    while (got < count && millis() < deadline) {
        if (Serial.available()) buf[got++] = Serial.read();
        else delayMicroseconds(50);
    }
    return got;
}

// ── Command dispatcher ────────────────────────────────────────────────────
static void _process_command(const char* cmd) {

    // CMD:PING
    if (strcmp(cmd, "CMD:PING") == 0) {
        Serial.println("ACK:PING");
        return;
    }

    // CMD:GET_FUEL_MAP
    if (strcmp(cmd, "CMD:GET_FUEL_MAP") == 0) {
        Serial.print("MAP:FUEL,");
        for (int i = 0; i < MAP_SIZE; i++) {
            Serial.print(romData[FUEL_MAP_ADDR + i]);
            if (i < MAP_SIZE - 1) Serial.print(",");
        }
        Serial.println();
        return;
    }

    // CMD:GET_TIMING_MAP
    if (strcmp(cmd, "CMD:GET_TIMING_MAP") == 0) {
        Serial.print("MAP:TIMING,");
        for (int i = 0; i < MAP_SIZE; i++) {
            Serial.print(romData[TIMING_MAP_ADDR + i]);
            if (i < MAP_SIZE - 1) Serial.print(",");
        }
        Serial.println();
        return;
    }

    // CMD:SET_CELL,fuel|timing,row,col,value
    if (strncmp(cmd, "CMD:SET_CELL,", 13) == 0) {
        char type[8];
        int row, col, val;
        if (sscanf(cmd + 13, "%7[^,],%d,%d,%d", type, &row, &col, &val) == 4) {
            if (row >= 0 && row < MAP_ROWS && col >= 0 && col < MAP_COLS && val >= 0 && val <= 255) {
                uint16_t base = (strcmp(type, "timing") == 0) ? TIMING_MAP_ADDR : FUEL_MAP_ADDR;
                romData[base + row * MAP_COLS + col] = (uint8_t)val;
                Serial.println("ACK:SET_CELL");
                return;
            }
        }
        Serial.println("ERR:SET_CELL_PARSE");
        return;
    }

    // CMD:LOAD_ROM,filename
    if (strncmp(cmd, "CMD:LOAD_ROM,", 13) == 0) {
        const char* fname = cmd + 13;
        load_rom_from_sd(fname);
        Serial.printf("ACK:LOAD_ROM,%s\n", fname);
        Serial.printf("$STATUS,ready,%lu,%s\n", millis(), fname);
        return;
    }

    // CMD:LIST_ROMS
    if (strcmp(cmd, "CMD:LIST_ROMS") == 0) {
        char buf[512] = {0};
        list_sd_bin_files(buf, sizeof(buf));
        Serial.printf("ROMS:%s\n", buf);
        return;
    }

    // CMD:SAVE_MAP
    if (strcmp(cmd, "CMD:SAVE_MAP") == 0) {
        save_tune_to_sd(g_rom_file);
        Serial.println("ACK:SAVE_MAP");
        return;
    }

    // CMD:CORRECTIONS_ON
    if (strcmp(cmd, "CMD:CORRECTIONS_ON") == 0) {
        g_corrections_enabled = true;
        Serial.println("ACK:CORRECTIONS_ON");
        return;
    }

    // CMD:CORRECTIONS_OFF
    if (strcmp(cmd, "CMD:CORRECTIONS_OFF") == 0) {
        g_corrections_enabled = false;
        Serial.println("ACK:CORRECTIONS_OFF");
        return;
    }

    // CMD:SET_TARGET_AFR,value
    if (strncmp(cmd, "CMD:SET_TARGET_AFR,", 19) == 0) {
        float val = atof(cmd + 19);
        if (val >= 10.0f && val <= 18.0f) {
            g_target_afr = val;
            Serial.printf("ACK:SET_TARGET_AFR,%.2f\n", val);
        } else {
            Serial.println("ERR:AFR_RANGE");
        }
        return;
    }

    // CMD:ROM_DOWNLOAD,filename
    // Sends the full .bin from SD card to PC in 256-byte chunks with CRC32
    if (strncmp(cmd, "CMD:ROM_DOWNLOAD,", 17) == 0) {
        const char* fname = cmd + 17;
        // Open file from SD
        File f = SD.open(fname, FILE_READ);
        if (!f) {
            Serial.printf("ERR:ROM_NOT_FOUND,%s\n", fname);
            return;
        }
        uint32_t fsize = f.size();
        // Send transfer start header
        Serial.printf("XFER:START,%s,%lu\n", fname, fsize);

        uint8_t  chunk_buf[256];
        uint32_t crc    = 0;
        uint32_t offset = 0;
        int      idx    = 0;
        while (offset < fsize) {
            int len = f.read(chunk_buf, 256);
            if (len <= 0) break;
            // Update CRC
            for (int i = 0; i < len; i++) crc = _crc32_update(crc, chunk_buf[i]);
            // Send chunk header then raw bytes
            Serial.printf("XFER:CHUNK,%d,%d\n", idx, len);
            Serial.write(chunk_buf, len);
            Serial.write('\n');
            Serial.flush();
            offset += len;
            idx++;
            delay(2);  // Small delay so PC can keep up
        }
        f.close();
        crc ^= 0xFFFFFFFF;
        Serial.printf("XFER:END,%lu\n", crc);
        return;
    }

    // CMD:ROM_UPLOAD,filename,size
    // Receives a full .bin from PC and writes it to SD card
    if (strncmp(cmd, "CMD:ROM_UPLOAD,", 15) == 0) {
        char  fname[32] = {0};
        uint32_t expected_size = 0;
        if (sscanf(cmd + 15, "%31[^,],%lu", fname, &expected_size) != 2) {
            Serial.println("ERR:ROM_UPLOAD_PARSE");
            return;
        }
        if (expected_size == 0 || expected_size > 65536) {
            Serial.println("ERR:ROM_UPLOAD_SIZE");
            return;
        }

        Serial.println("ACK:ROM_UPLOAD_READY");

        // Now receive chunks until XFER:DONE,crc
        File f = SD.open(fname, FILE_WRITE | O_TRUNC);
        if (!f) {
            Serial.printf("ERR:ROM_UPLOAD_OPEN,%s\n", fname);
            return;
        }

        char     line_buf[64];
        uint8_t  chunk_buf[256];
        uint32_t rx_crc = 0;
        uint32_t rx_bytes = 0;
        bool     ok = true;
        uint32_t timeout_ms = millis() + 30000;  // 30s total timeout

        while (millis() < timeout_ms) {
            // Read a line (header or XFER:DONE)
            int line_len = _read_line_timeout(line_buf, sizeof(line_buf), 5000);
            if (line_len < 0) { ok = false; break; }

            if (strncmp(line_buf, "XFER:DONE,", 10) == 0) {
                uint32_t expected_crc = strtoul(line_buf + 10, nullptr, 10);
                rx_crc ^= 0xFFFFFFFF;
                if (rx_crc == expected_crc) {
                    f.flush();
                    f.close();
                    Serial.println("ACK:ROM_UPLOAD_COMPLETE");
                } else {
                    f.close();
                    Serial.printf("ERR:ROM_UPLOAD_CRC,%lu,%lu\n", expected_crc, rx_crc);
                }
                return;
            }

            if (strncmp(line_buf, "XFER:CHUNK,", 11) == 0) {
                int  chunk_idx = 0;
                int  chunk_len = 0;
                sscanf(line_buf + 11, "%d,%d", &chunk_idx, &chunk_len);
                if (chunk_len <= 0 || chunk_len > 256) { ok = false; break; }

                // Read exactly chunk_len bytes + trailing \n
                int got = _read_bytes_timeout(chunk_buf, chunk_len, 3000);
                if (got != chunk_len) { ok = false; break; }
                Serial.read();  // consume trailing \n

                f.write(chunk_buf, chunk_len);
                for (int i = 0; i < chunk_len; i++) rx_crc = _crc32_update(rx_crc, chunk_buf[i]);
                rx_bytes += chunk_len;
                timeout_ms = millis() + 10000;  // reset per-chunk timeout
                continue;
            }
        }

        f.close();
        if (!ok) Serial.println("ERR:ROM_UPLOAD_TIMEOUT");
        return;
    }

    // Unknown
    Serial.printf("ERR:UNKNOWN_CMD,%s\n", cmd);
}

#endif // USB_TUNE_IMPL
