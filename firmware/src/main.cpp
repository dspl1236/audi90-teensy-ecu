// =============================================================================
//  main.cpp — TeensyEprom v2.0
//
//  EPROM emulator + map switcher for Teensy 4.1.
//  Replaces 27C256 / 27C512 in Hitachi MMS ECUs (7A 20v, AAH V6, etc).
//
//  Storage: LittleFS on program flash (primary), SD card (fallback).
//  ROMs uploaded via USB serial from desktop app — no SD card needed.
//
//  No wideband, no injector intercept, no CAN, no corrections.
// =============================================================================

#include <Arduino.h>
#include <LittleFS.h>
#include <SD.h>
#include "config.h"

// -- Filesystem ---------------------------------------------------------------
LittleFS_Program lfs;
static bool g_lfs_ok = false;
static bool g_sd_ok  = false;

// -- ROM buffer ---------------------------------------------------------------
static volatile uint8_t g_rom[ROM_SIZE];
static volatile bool    g_emulating = false;

// -- Map switcher state -------------------------------------------------------
static char     g_mapFiles[MAX_MAPS][MAX_FILENAME];
static uint8_t  g_mapCount  = 0;
static uint8_t  g_activeMap = 0;
static bool     g_fromSD    = false;    // true if maps loaded from SD fallback

// =============================================================================
//  Fast GPIO — EPROM bus interface
// =============================================================================

FASTRUN static inline uint16_t read_address() {
    uint16_t addr = 0;
    for (uint8_t i = 0; i < 16; i++) {
        if (digitalReadFast(ADDR_PINS[i])) addr |= (1u << i);
    }
    return addr;
}

FASTRUN static inline void write_data(uint8_t val) {
    for (uint8_t i = 0; i < 8; i++) {
        digitalWriteFast(DATA_PINS[i], (val >> i) & 1);
    }
}

static inline void data_bus_output() {
    for (uint8_t i = 0; i < 8; i++) pinMode(DATA_PINS[i], OUTPUT);
}

static inline void data_bus_hiZ() {
    for (uint8_t i = 0; i < 8; i++) pinMode(DATA_PINS[i], INPUT);
}

// =============================================================================
//  EPROM emulation ISR
// =============================================================================
//
// /OE falling edge -> read address -> lookup -> drive data -> hold -> release.
// HD6303 bus cycle ~500ns. ISR total ~250ns. Plenty of margin.

FASTRUN void oe_isr() {
    if (!g_emulating) return;
    if (digitalReadFast(PIN_CE)) return;   // /CE must be LOW

    uint16_t addr = read_address();
    uint8_t  val  = g_rom[addr & 0xFFFF];

#if DATA_BUS_BUFFERED
    // U3 74HCT245 handles tri-state via /OE — just write data
    write_data(val);
#else
    // Direct data bus — must manage direction and hold until /OE released
    data_bus_output();
    write_data(val);

    // Wait for /OE HIGH with timeout to prevent hang if /OE stuck
    uint32_t t0 = ARM_DWT_CYCCNT;
    uint32_t limit = ISR_TIMEOUT_US * (F_CPU / 1000000);
    while (!digitalReadFast(PIN_OE)) {
        if ((ARM_DWT_CYCCNT - t0) > limit) break;
    }

    data_bus_hiZ();
#endif
}

// =============================================================================
//  CRC16-CCITT (XMODEM variant)
// =============================================================================

static uint16_t crc16(const uint8_t* data, size_t len) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (uint8_t j = 0; j < 8; j++) {
            crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : crc << 1;
        }
    }
    return crc;
}

// =============================================================================
//  ROM loading
// =============================================================================

static bool load_rom_from_file(FS& fs, const char* filename) {
    File f = fs.open(filename, FILE_READ);
    if (!f) {
        Serial.print("ERR: cannot open ");
        Serial.println(filename);
        return false;
    }

    size_t sz = f.size();
    if (sz < ROM_ACTIVE_SIZE) {
        Serial.print("ERR: too small (");
        Serial.print(sz);
        Serial.println("B)");
        f.close();
        return false;
    }

    // Pause emulation during load
    g_emulating = false;
    noInterrupts();

    // Read into temp buffer to avoid casting away volatile
    uint8_t tmp[ROM_ACTIVE_SIZE];
    f.read(tmp, ROM_ACTIVE_SIZE);
    for (uint32_t i = 0; i < ROM_ACTIVE_SIZE; i++) g_rom[i] = tmp[i];

    if (sz >= ROM_SIZE) {
        // Full 64KB — read upper half
        f.read(tmp, ROM_ACTIVE_SIZE);
        for (uint32_t i = 0; i < ROM_ACTIVE_SIZE; i++) g_rom[ROM_ACTIVE_SIZE + i] = tmp[i];
    } else {
        // 32KB — mirror into upper half (A15-agnostic)
        for (uint32_t i = 0; i < ROM_ACTIVE_SIZE; i++) g_rom[ROM_ACTIVE_SIZE + i] = g_rom[i];
    }

    interrupts();
    g_emulating = true;
    f.close();

    Serial.print("OK: ");
    Serial.print(filename);
    Serial.print(" (");
    Serial.print(sz);
    Serial.println("B)");
    return true;
}

static bool load_active_map() {
    if (g_mapCount == 0) return false;
    if (g_fromSD) {
        return load_rom_from_file(SD, g_mapFiles[g_activeMap]);
    } else {
        return load_rom_from_file(lfs, g_mapFiles[g_activeMap]);
    }
}

// =============================================================================
//  Map scanning — LittleFS primary, SD fallback
// =============================================================================

static uint8_t scan_fs(FS& fs, const char* label) {
    uint8_t count = 0;
    File dir = fs.open(MAP_DIR);
    if (!dir || !dir.isDirectory()) {
        // Try creating the directory on LittleFS
        if (&fs == &lfs) {
            lfs.mkdir(MAP_DIR);
            dir = fs.open(MAP_DIR);
            if (!dir || !dir.isDirectory()) return 0;
        } else {
            return 0;
        }
    }

    File entry;
    while ((entry = dir.openNextFile()) && count < MAX_MAPS) {
        if (!entry.isDirectory()) {
            const char* name = entry.name();
            size_t len = strlen(name);
            if (len > 4 && len < (MAX_FILENAME - 7) &&
                strcasecmp(name + len - 4, ".bin") == 0) {
                snprintf(g_mapFiles[count], MAX_FILENAME,
                         "%s%s", MAP_DIR, name);
                count++;
            }
        }
        entry.close();
    }
    dir.close();

    if (count > 0) {
        Serial.print(label);
        Serial.print(": ");
        Serial.print(count);
        Serial.println(" map(s)");
    }
    return count;
}

static void scan_maps() {
    g_mapCount = 0;
    g_fromSD   = false;

    // Primary: LittleFS
    if (g_lfs_ok) {
        g_mapCount = scan_fs(lfs, "LittleFS");
    }

    // Fallback: SD card (only if LittleFS had nothing)
    if (g_mapCount == 0 && g_sd_ok) {
        g_mapCount = scan_fs(SD, "SD card");
        if (g_mapCount > 0) g_fromSD = true;
    }

    // List all found maps
    for (uint8_t i = 0; i < g_mapCount; i++) {
        Serial.print("  ["); Serial.print(i); Serial.print("] ");
        Serial.println(g_mapFiles[i]);
    }
}

// =============================================================================
//  LED
// =============================================================================

static void led_blink(uint8_t count, uint16_t on_ms = LED_BLINK_MS) {
    for (uint8_t i = 0; i < count; i++) {
        digitalWriteFast(PIN_LED, HIGH); delay(on_ms);
        digitalWriteFast(PIN_LED, LOW);  delay(on_ms);
    }
}

static void led_error_signal() {
    // 10 fast blinks = load error (but don't block — allow USB recovery)
    led_blink(10, 50);
}

// =============================================================================
//  Button — map cycling
// =============================================================================

static void button_check() {
    static uint32_t press_start = 0;
    static bool     was_pressed = false;

    bool pressed = !digitalReadFast(PIN_BUTTON);

    if (pressed && !was_pressed) {
        press_start = millis();
        was_pressed = true;
    }

    if (!pressed && was_pressed) {
        uint32_t held = millis() - press_start;
        was_pressed = false;

        if (g_mapCount == 0) return;

        if (held >= BUTTON_HOLD_MS) {
            // Long press: previous map
            g_activeMap = (g_activeMap == 0) ? g_mapCount - 1 : g_activeMap - 1;
        } else if (held >= DEBOUNCE_MS) {
            // Short press: next map
            g_activeMap = (g_activeMap + 1) % g_mapCount;
        } else {
            return;  // too short, ignore
        }

        Serial.print("-> ["); Serial.print(g_activeMap); Serial.print("] ");
        Serial.println(g_mapFiles[g_activeMap]);
        load_active_map();
        led_blink(g_activeMap + 1);
    }
}

// =============================================================================
//  USB serial commands
// =============================================================================

static void cmd_info() {
    Serial.print(IDENT_STRING);
    Serial.print("Storage: ");
    Serial.println(g_fromSD ? "SD card (fallback)" : "LittleFS (flash)");
    Serial.print("LittleFS: ");
    Serial.println(g_lfs_ok ? "OK" : "FAIL");
    Serial.print("SD card: ");
    Serial.println(g_sd_ok ? "OK" : "not present");
    Serial.print("Maps: ");
    Serial.println(g_mapCount);
    if (g_mapCount > 0) {
        Serial.print("Active: [");
        Serial.print(g_activeMap);
        Serial.print("] ");
        Serial.println(g_mapFiles[g_activeMap]);
    }
    Serial.print("Emulating: ");
    Serial.println(g_emulating ? "YES" : "NO");
}

static void cmd_list() {
    if (g_mapCount == 0) {
        Serial.println("(no maps)");
        return;
    }
    for (uint8_t i = 0; i < g_mapCount; i++) {
        Serial.print(i == g_activeMap ? "* " : "  ");
        Serial.print("["); Serial.print(i); Serial.print("] ");
        Serial.println(g_mapFiles[i]);
    }
}

static void cmd_switch(int idx) {
    if (idx < 0 || idx >= g_mapCount) {
        Serial.println("ERR: bad index");
        return;
    }
    g_activeMap = idx;
    load_active_map();
    led_blink(g_activeMap + 1);
}

static void cmd_dump() {
    // Hex dump first 256 bytes of active ROM
    for (int row = 0; row < 16; row++) {
        char buf[8];
        snprintf(buf, sizeof(buf), "%04X: ", row * 16);
        Serial.print(buf);
        for (int col = 0; col < 16; col++) {
            uint8_t b = g_rom[row * 16 + col];
            if (b < 0x10) Serial.print("0");
            Serial.print(b, HEX);
            Serial.print(" ");
        }
        Serial.println();
    }
}

static void cmd_upload(const String& args) {
    // Parse: UPLOAD filename.bin 32768
    int space = args.indexOf(' ');
    if (space < 0) {
        Serial.println("ERR: usage: UPLOAD <filename> <size>");
        return;
    }

    String filename = args.substring(0, space);
    uint32_t size = args.substring(space + 1).toInt();

    if (size < ROM_ACTIVE_SIZE || size > ROM_SIZE) {
        Serial.print("ERR: size must be ");
        Serial.print(ROM_ACTIVE_SIZE);
        Serial.print("-");
        Serial.println(ROM_SIZE);
        return;
    }

    if (!g_lfs_ok) {
        Serial.println("ERR: LittleFS not available");
        return;
    }

    // Build full path
    char path[MAX_FILENAME];
    snprintf(path, sizeof(path), "%s%s", MAP_DIR, filename.c_str());

    Serial.println("READY");  // Signal desktop app to start sending

    // Receive raw bytes
    uint8_t* buf = (uint8_t*)malloc(size);
    if (!buf) {
        Serial.println("ERR: out of memory");
        return;
    }

    uint32_t received = 0;
    uint32_t start_ms = millis();

    while (received < size) {
        if (millis() - start_ms > UPLOAD_TIMEOUT_MS) {
            free(buf);
            Serial.println("ERR: timeout");
            return;
        }
        if (Serial.available()) {
            buf[received++] = Serial.read();
            start_ms = millis();  // reset timeout on each byte
        }
    }

    // Read 2-byte CRC16 (big-endian)
    uint32_t crc_start = millis();
    uint8_t crc_buf[2];
    uint8_t crc_got = 0;
    while (crc_got < 2) {
        if (millis() - crc_start > UPLOAD_TIMEOUT_MS) {
            free(buf);
            Serial.println("ERR: CRC timeout");
            return;
        }
        if (Serial.available()) {
            crc_buf[crc_got++] = Serial.read();
            crc_start = millis();
        }
    }

    uint16_t crc_rx = ((uint16_t)crc_buf[0] << 8) | crc_buf[1];
    uint16_t crc_calc = crc16(buf, size);

    if (crc_rx != crc_calc) {
        free(buf);
        Serial.print("ERR: CRC mismatch (got 0x");
        Serial.print(crc_rx, HEX);
        Serial.print(", calc 0x");
        Serial.print(crc_calc, HEX);
        Serial.println(")");
        return;
    }

    // Ensure /maps/ directory exists
    lfs.mkdir(MAP_DIR);

    // Write to LittleFS
    File f = lfs.open(path, FILE_WRITE);
    if (!f) {
        free(buf);
        Serial.println("ERR: cannot create file");
        return;
    }
    f.write(buf, size);
    f.close();
    free(buf);

    Serial.print("OK: ");
    Serial.print(path);
    Serial.print(" (");
    Serial.print(size);
    Serial.print("B, CRC 0x");
    Serial.print(crc_calc, HEX);
    Serial.println(")");

    // Rescan maps and load the new file
    scan_maps();
    // Find the uploaded file and switch to it
    for (uint8_t i = 0; i < g_mapCount; i++) {
        if (strcmp(g_mapFiles[i], path) == 0) {
            g_activeMap = i;
            load_active_map();
            led_blink(2, 100);
            break;
        }
    }
}

static bool parse_index(const String& args, int& idx) {
    if (args.length() == 0 || !isDigit(args.charAt(0))) {
        Serial.println("ERR: expected numeric index");
        return false;
    }
    idx = args.toInt();
    return true;
}

static void cmd_download(const String& args) {
    if (!g_lfs_ok && !g_sd_ok) {
        Serial.println("ERR: no filesystem");
        return;
    }

    int idx;
    if (!parse_index(args, idx)) return;
    if (idx < 0 || idx >= g_mapCount) {
        Serial.println("ERR: bad index");
        return;
    }

    FS& fs = g_fromSD ? (FS&)SD : (FS&)lfs;
    File f = fs.open(g_mapFiles[idx], FILE_READ);
    if (!f) {
        Serial.println("ERR: cannot open");
        return;
    }

    size_t sz = f.size();
    uint8_t* buf = (uint8_t*)malloc(sz);
    if (!buf) {
        f.close();
        Serial.println("ERR: out of memory");
        return;
    }

    f.read(buf, sz);
    f.close();

    uint16_t crc = crc16(buf, sz);

    Serial.print("SIZE ");
    Serial.println(sz);

    Serial.write(buf, sz);
    Serial.write((uint8_t)(crc >> 8));
    Serial.write((uint8_t)(crc & 0xFF));

    free(buf);
}

static void cmd_delete(const String& args) {
    if (!g_lfs_ok) {
        Serial.println("ERR: LittleFS not available");
        return;
    }

    int idx;
    if (!parse_index(args, idx)) return;
    if (idx < 0 || idx >= g_mapCount) {
        Serial.println("ERR: bad index");
        return;
    }

    if (g_fromSD) {
        Serial.println("ERR: cannot delete from SD card");
        return;
    }

    const char* path = g_mapFiles[idx];
    if (lfs.remove(path)) {
        Serial.print("OK: deleted ");
        Serial.println(path);
        // If we deleted the active map, switch to 0
        if (idx == g_activeMap) g_activeMap = 0;
        scan_maps();
        if (g_mapCount > 0) load_active_map();
    } else {
        Serial.println("ERR: delete failed");
    }
}

static void cmd_format() {
    if (!g_lfs_ok) {
        Serial.println("ERR: LittleFS not available");
        return;
    }

    Serial.println("Formatting LittleFS...");
    g_emulating = false;

    // Remove all files in /maps/
    File dir = lfs.open(MAP_DIR);
    if (dir && dir.isDirectory()) {
        File entry;
        while ((entry = dir.openNextFile())) {
            char path[MAX_FILENAME];
            snprintf(path, sizeof(path), "%s%s", MAP_DIR, entry.name());
            entry.close();
            lfs.remove(path);
        }
        dir.close();
    }

    g_mapCount  = 0;
    g_activeMap = 0;
    Serial.println("OK: all maps deleted");
}

// -- Command dispatcher -------------------------------------------------------

static void usb_command(const String& cmd) {
    if (cmd == "INFO") {
        cmd_info();
    } else if (cmd == "LIST") {
        cmd_list();
    } else if (cmd.startsWith("MAP ")) {
        cmd_switch(cmd.substring(4).toInt());
    } else if (cmd == "DUMP") {
        cmd_dump();
    } else if (cmd.startsWith("UPLOAD ")) {
        cmd_upload(cmd.substring(7));
    } else if (cmd.startsWith("DOWNLOAD ")) {
        cmd_download(cmd.substring(9));
    } else if (cmd.startsWith("DELETE ")) {
        cmd_delete(cmd.substring(7));
    } else if (cmd == "FORMAT") {
        cmd_format();
    } else if (cmd == "SCAN") {
        scan_maps();
        if (g_mapCount > 0) {
            g_activeMap = 0;
            load_active_map();
        }
    } else {
        Serial.println("Commands: INFO LIST MAP DUMP UPLOAD DOWNLOAD DELETE FORMAT SCAN");
    }
}

static void usb_read() {
    static char buf[CMD_BUF_SIZE];
    static uint8_t buf_len = 0;
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n' || c == '\r') {
            if (buf_len > 0) {
                buf[buf_len] = '\0';
                // Trim leading/trailing spaces
                char* start = buf;
                while (*start == ' ') start++;
                char* end = buf + buf_len - 1;
                while (end > start && *end == ' ') *end-- = '\0';
                if (*start) usb_command(String(start));
            }
            buf_len = 0;
        } else if (buf_len < CMD_BUF_SIZE - 1) {
            buf[buf_len++] = c;
        }
    }
}

// =============================================================================
//  setup / loop
// =============================================================================

void setup() {
    Serial.begin(SERIAL_BAUD);
    delay(300);
    Serial.print(IDENT_STRING);

    // -- GPIO init --
    pinMode(PIN_LED, OUTPUT);
    digitalWriteFast(PIN_LED, LOW);
    for (uint8_t i = 0; i < 16; i++) pinMode(ADDR_PINS[i], INPUT);
#if DATA_BUS_BUFFERED
    // U3 74HCT245 handles tri-state — keep data pins as OUTPUT permanently
    data_bus_output();
    write_data(0xFF);
#else
    data_bus_hiZ();
#endif
    pinMode(PIN_OE, INPUT);
    pinMode(PIN_CE, INPUT);
    pinMode(PIN_BUTTON, INPUT_PULLUP);

    // Enable cycle counter for ISR timeout
    ARM_DEMCR |= ARM_DEMCR_TRCENA;
    ARM_DWT_CTRL |= ARM_DWT_CTRL_CYCCNTENA;

    // -- LittleFS (primary) --
    Serial.print("LittleFS: ");
    g_lfs_ok = lfs.begin(LITTLEFS_SIZE);
    Serial.println(g_lfs_ok ? "OK" : "FAIL");

    if (g_lfs_ok) {
        // Ensure /maps/ directory exists
        lfs.mkdir(MAP_DIR);
    }

    // -- SD card (fallback) --
    Serial.print("SD: ");
    g_sd_ok = SD.begin(BUILTIN_SDCARD);
    Serial.println(g_sd_ok ? "OK" : "not present");

    // -- Scan for ROM files --
    scan_maps();

    if (g_mapCount == 0) {
        Serial.println("No maps found — upload via USB: UPLOAD <name.bin> <size>");
        Serial.println("Waiting...");
        led_blink(5, 100);  // fast blink = no maps but alive
    } else if (!load_active_map()) {
        Serial.println("ERR: failed to load map — upload a new one via USB");
        led_error_signal();
        // Fall through to loop() so USB commands still work
    } else {
        attachInterrupt(digitalPinToInterrupt(PIN_OE), oe_isr, FALLING);
        Serial.println("EPROM active");
        led_blink(3, 300);
    }
}

void loop() {
    button_check();
    usb_read();

    // If we started with no maps but user uploaded one, start emulating
    if (!g_emulating && g_mapCount > 0) {
        if (load_active_map()) {
            attachInterrupt(digitalPinToInterrupt(PIN_OE), oe_isr, FALLING);
            Serial.println("EPROM active");
            led_blink(3, 300);
        }
    }
}
