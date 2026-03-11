"""
ui/hardware_config_tab.py
Hardware Mod Builder — start from a known base ROM, check what you changed,
get a ready-to-burn .bin.

v2.0.0
"""

import os
import math
import zlib
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QPushButton, QLabel, QComboBox, QFileDialog,
    QMessageBox, QFrame, QCheckBox, QSpinBox,
    QGridLayout, QTextEdit, QSizePolicy
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QFont

from ecu_profiles import (
    unscramble_rom, detect_ecu_version, apply_checksum, verify_checksum,
    get_fuel_map_def, INJECTOR_PROFILES, KNOWN_ROM_LIBRARY,
    DetectionResult,
)


# ---------------------------------------------------------------------------
# Known base ROMs — what each file was tuned for
# ---------------------------------------------------------------------------

BASE_ROMS = {
    # key: (label, ecu_version, base_disp_cc, injector_key, notes)
    "266D_STOCK":  ("Stock 266D  (OEM 7A Late)",        "266D", 2309, "STOCK_7A",
                    "Factory stock map. Full closed-loop, conservative timing."),
    "266D_S1":     ("034 Stage 1 266D  (NA 91oct)",     "266D", 2309, "STOCK_7A",
                    "034 Stage 1 NA. Optimised timing, stock injectors. No turbo."),
    "266D_S2_550": ("034 Stage 2 266D  (Turbo 550cc)",  "266D", 2309, "CC550",
                    "034 Turbo Stage 2. Built for ~2309cc + 550cc injectors + stock MAF housing. "
                    "Richer fuel map + raised injection scaler. Best starting point for a stroker turbo build."),
    "266B_STOCK":  ("Stock 266B  (OEM 7A Early)",       "266B", 2309, "STOCK_7A",
                    "Factory stock map for early 2-connector ECU."),
    "266B_S2_550": ("034 Stage 2 266B  (Turbo 550cc)",  "266B", 2309, "CC550",
                    "034 Turbo Stage 2 for early ECU."),
    "CUSTOM":      ("Custom / Unknown  (load file below)", None, 2309, "STOCK_7A",
                    "Load any .bin or .034 — ECU version and injectors will be auto-detected."),
}

def _s(v):
    """Format a scalar, highlighting when it's a no-op."""
    return f"x{v:.4f}" if abs(v - 1.0) > 0.0001 else "x1.0000  (no change)"

def _label(text, color="#3d5068", size=11, bold=False):
    l = QLabel(text)
    w = "bold;" if bold else ""
    l.setStyleSheet(f"color:{color}; font-size:{size}px; {w}")
    return l

def _separator():
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color: #1a2332; margin: 4px 0;")
    return f

_COMBO_STYLE = (
    "QComboBox { background:#0d1117; border:1px solid #1a2332; color:#bccdd8; padding:4px 8px; }"
    "QComboBox::drop-down { border:none; }"
    "QComboBox QAbstractItemView { background:#0d1117; color:#bccdd8; "
    "selection-background-color:#1a2332; }"
)
_SPIN_STYLE = (
    "QSpinBox { background:#0d1117; border:1px solid #1a2332; color:#bccdd8; padding:4px 8px; }"
)
_BTN_PRIMARY = (
    "QPushButton { background:#0a1a2e; color:#00d4ff; border:1px solid #00d4ff; "
    "padding:7px 18px; font-size:12px; }"
    "QPushButton:hover { background:#001a2e; color:#40e8ff; border-color:#40e8ff; }"
    "QPushButton:disabled { color:#2a3a4a; border-color:#1a2332; }"
)
_BTN_BUILD = (
    "QPushButton { background:#001a0a; color:#2dff6e; border:1px solid #2dff6e; "
    "padding:9px 24px; font-size:13px; font-weight:bold; }"
    "QPushButton:hover { background:#002a14; color:#60ff90; border-color:#60ff90; }"
    "QPushButton:disabled { color:#1a3a24; border-color:#1a2332; }"
)
_BTN_FLASH = (
    "QPushButton { background:#0a0e14; color:#ffa040; border:1px solid #ffa040; "
    "padding:6px 18px; font-size:11px; }"
    "QPushButton:hover { background:#1a1000; color:#ffb860; border-color:#ffb860; }"
)
_CHK_STYLE = "QCheckBox { color:#bccdd8; font-size:12px; spacing:8px; }"


# ---------------------------------------------------------------------------

class HardwareConfigTab(QWidget):
    sig_config_changed = pyqtSignal(dict)
    sig_flash_firmware = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._teensy      = None
        self._rom_data    = None   # bytes — native (unscrambled) 32KB or 64KB
        self._ecu_version = "266D"
        self._base_key    = "266D_S2_550"
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)

        root.addWidget(_label(
            "Start from a known base tune, describe what hardware you changed, get a ready-to-burn .bin.",
            color="#7a9ab0", size=11))

        # ── Step 1: Base ROM ────────────────────────────────────────────────
        grp1 = QGroupBox("Step 1  —  Base ROM  (which tune are you starting from?)")
        g1 = QVBoxLayout(grp1)

        base_row = QHBoxLayout()
        base_row.addWidget(_label("Base tune:"))
        self.cmb_base = QComboBox()
        self.cmb_base.setStyleSheet(_COMBO_STYLE)
        for key, (label, *_) in BASE_ROMS.items():
            self.cmb_base.addItem(label, key)
        self.cmb_base.setCurrentIndex(list(BASE_ROMS).index("266D_S2_550"))
        self.cmb_base.currentIndexChanged.connect(self._on_base_changed)
        base_row.addWidget(self.cmb_base, 1)
        g1.addLayout(base_row)

        self.lbl_base_notes = _label("", color="#3d5068", size=11)
        self.lbl_base_notes.setWordWrap(True)
        g1.addWidget(self.lbl_base_notes)

        # Load row (only visible for CUSTOM)
        load_row = QHBoxLayout()
        self.btn_load_rom = QPushButton("📂  Load ROM File  (.bin / .034)...")
        self.btn_load_rom.setStyleSheet(_BTN_PRIMARY)
        self.btn_load_rom.clicked.connect(self._load_rom)
        self.lbl_loaded   = _label("No file loaded", color="#3d5068", size=11)
        load_row.addWidget(self.btn_load_rom)
        load_row.addSpacing(10)
        load_row.addWidget(self.lbl_loaded)
        load_row.addStretch()
        g1.addLayout(load_row)
        self.lbl_detected = _label("", color="#3d5068", size=11)
        g1.addWidget(self.lbl_detected)

        root.addWidget(grp1)

        # ── Step 2: What changed ────────────────────────────────────────────
        grp2 = QGroupBox("Step 2  —  What hardware did you change?")
        g2 = QVBoxLayout(grp2)
        g2.setSpacing(10)

        # Displacement
        self.chk_disp = QCheckBox("Engine displacement changed")
        self.chk_disp.setStyleSheet(_CHK_STYLE)
        self.chk_disp.setToolTip(
            "Stroker or bore-up changes air volume per cycle.\n"
            "The MAF sees exactly the same airflow, but the ECU calculates\n"
            "fuelling assuming stock displacement.\n\n"
            "Scaling the fuel map by (new_cc / base_cc) corrects the AFR.")
        self.chk_disp.stateChanged.connect(self._on_options_changed)
        g2.addWidget(self.chk_disp)

        disp_detail = QHBoxLayout()
        disp_detail.setContentsMargins(28, 0, 0, 0)
        disp_detail.addWidget(_label("Base ROM tuned for:"))
        self.spn_disp_from = QSpinBox()
        self.spn_disp_from.setRange(1800, 3500)
        self.spn_disp_from.setValue(2309)
        self.spn_disp_from.setSuffix(" cc")
        self.spn_disp_from.setFixedWidth(105)
        self.spn_disp_from.setStyleSheet(_SPIN_STYLE)
        self.spn_disp_from.setToolTip("Displacement the base ROM was tuned for (stock 7A = 2309 cc)")
        self.spn_disp_from.valueChanged.connect(self._on_options_changed)
        disp_detail.addWidget(self.spn_disp_from)
        disp_detail.addWidget(_label("→   Your engine:"))
        self.spn_disp_to = QSpinBox()
        self.spn_disp_to.setRange(1800, 3500)
        self.spn_disp_to.setValue(2553)
        self.spn_disp_to.setSuffix(" cc")
        self.spn_disp_to.setFixedWidth(105)
        self.spn_disp_to.setStyleSheet(_SPIN_STYLE)
        self.spn_disp_to.setToolTip("Your actual engine displacement in cc\n"
                                     "2.6L stroker (AAF crank 95.6mm, stock bore) ≈ 2553 cc")
        self.spn_disp_to.valueChanged.connect(self._on_options_changed)
        disp_detail.addWidget(self.spn_disp_to)
        self.lbl_disp_scalar = _label("", color="#00d4ff", size=11)
        disp_detail.addWidget(self.lbl_disp_scalar)
        disp_detail.addStretch()
        g2.addLayout(disp_detail)

        g2.addWidget(_separator())

        # Injectors
        self.chk_inj = QCheckBox("Injectors swapped")
        self.chk_inj.setStyleSheet(_CHK_STYLE)
        self.chk_inj.setToolTip(
            "Larger injectors flow more fuel per ms of pulse width.\n"
            "Scaling the fuel map DOWN by the flow ratio keeps AFR correct.\n\n"
            "Flow is pressure-normalized to 4.0 bar (stock 7A rail pressure).\n"
            "Scale = from_cc_at_4bar / to_cc_at_4bar")
        self.chk_inj.stateChanged.connect(self._on_options_changed)
        g2.addWidget(self.chk_inj)

        inj_detail = QHBoxLayout()
        inj_detail.setContentsMargins(28, 0, 0, 0)
        inj_detail.addWidget(_label("Base ROM injectors:"))
        self.cmb_inj_from = QComboBox()
        self.cmb_inj_from.setStyleSheet(_COMBO_STYLE)
        for key, p in INJECTOR_PROFILES.items():
            self.cmb_inj_from.addItem(
                f"{p.display}  ({p.cc_at_4bar:.0f} cc @ 4 bar)", key)
        self.cmb_inj_from.currentIndexChanged.connect(self._on_options_changed)
        inj_detail.addWidget(self.cmb_inj_from)
        inj_detail.addWidget(_label("→   Your injectors:"))
        self.cmb_inj_to = QComboBox()
        self.cmb_inj_to.setStyleSheet(_COMBO_STYLE)
        for key, p in INJECTOR_PROFILES.items():
            self.cmb_inj_to.addItem(
                f"{p.display}  ({p.cc_at_4bar:.0f} cc @ 4 bar)", key)
        self.cmb_inj_to.currentIndexChanged.connect(self._on_options_changed)
        inj_detail.addWidget(self.cmb_inj_to)
        self.lbl_inj_scalar = _label("", color="#00d4ff", size=11)
        inj_detail.addWidget(self.lbl_inj_scalar)
        inj_detail.addStretch()
        g2.addLayout(inj_detail)

        root.addWidget(grp2)

        # ── Step 3: Preview + Build ─────────────────────────────────────────
        grp3 = QGroupBox("Step 3  —  Preview & Build")
        g3 = QVBoxLayout(grp3)

        self.txt_preview = QTextEdit()
        self.txt_preview.setReadOnly(True)
        self.txt_preview.setMaximumHeight(120)
        self.txt_preview.setFont(QFont("Courier New", 10))
        self.txt_preview.setStyleSheet(
            "QTextEdit { background:#060a0f; border:1px solid #1a2332; "
            "color:#7a9ab0; font-family:'Courier New',monospace; font-size:11px; }")
        self.txt_preview.setPlainText(
            "Select a base tune and check what changed to see a build preview here.")
        g3.addWidget(self.txt_preview)

        build_row = QHBoxLayout()
        self.btn_build = QPushButton("⚡  Build .bin")
        self.btn_build.setStyleSheet(_BTN_BUILD)
        self.btn_build.setEnabled(False)
        self.btn_build.clicked.connect(self._build)
        self.lbl_build_result = _label("", color="#2dff6e", size=11)
        self.lbl_build_result.setWordWrap(True)
        build_row.addWidget(self.btn_build)
        build_row.addSpacing(12)
        build_row.addWidget(self.lbl_build_result, 1)
        g3.addLayout(build_row)

        root.addWidget(grp3)

        # ── Firmware flash ──────────────────────────────────────────────────
        flash_row = QHBoxLayout()
        self.btn_flash_fw = QPushButton("⚡  Flash Firmware to Teensy")
        self.btn_flash_fw.setStyleSheet(_BTN_FLASH)
        self.btn_flash_fw.setToolTip("Flash Teensy firmware via USB")
        self.btn_flash_fw.clicked.connect(self.sig_flash_firmware.emit)
        flash_row.addStretch()
        flash_row.addWidget(self.btn_flash_fw)
        root.addLayout(flash_row)

        root.addStretch()

        # Init display
        self._on_base_changed()

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _on_base_changed(self):
        key = self.cmb_base.currentData()
        self._base_key = key
        entry = BASE_ROMS.get(key)
        if not entry:
            return
        label, ecu_version, base_disp, inj_key, notes = entry

        self.lbl_base_notes.setText(notes)

        is_custom = (key == "CUSTOM")
        self.btn_load_rom.setVisible(is_custom)
        self.lbl_loaded.setVisible(is_custom)
        self.lbl_detected.setVisible(is_custom)

        # Pre-fill spinners from known profile
        if base_disp:
            self.spn_disp_from.setValue(base_disp)
        if inj_key:
            idx = self.cmb_inj_from.findData(inj_key)
            if idx >= 0:
                self.cmb_inj_from.setCurrentIndex(idx)

        if ecu_version:
            self._ecu_version = ecu_version

        if not is_custom:
            self._load_builtin_rom(key)

        self._on_options_changed()

    def _load_builtin_rom(self, key: str):
        """Try to load a matching .034 from the repo rom_files directory."""
        FILE_MAP = {
            "266D_STOCK":  "rom_files/034_rip_chip/034 - 893906266D Stock.034",
            "266D_S2_550": "rom_files/034_rip_chip/034 (Audi CQ (7a Turbo Stage 2 550cc 91 R1) - ) - 893906266D.034",
        }
        rel = FILE_MAP.get(key)
        if not rel:
            self._rom_data = None
            return

        # Walk up from this file to repo root
        repo_root = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", ".."))
        full = os.path.join(repo_root, rel)
        if not os.path.exists(full):
            self._rom_data = None
            return

        try:
            with open(full, "rb") as f:
                raw = f.read()
            self._rom_data = unscramble_rom(raw) if full.lower().endswith(".034") else raw
            version = BASE_ROMS[key][1]
            if version:
                self._ecu_version = version
        except Exception:
            self._rom_data = None

    def _load_rom(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Base ROM", "",
            "ROM Files (*.bin *.034);;All Files (*)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                raw = f.read()
            is_034 = path.lower().endswith(".034")
            native = unscramble_rom(raw) if is_034 else raw
            self._rom_data = native

            result = detect_ecu_version(native[:32768])
            self._ecu_version = result.version
            crc    = zlib.crc32(native[:32768]) & 0xFFFFFFFF
            cs_ok  = verify_checksum(bytes(native[:32768]), result.version)

            self.lbl_loaded.setText(
                f"✓  {os.path.basename(path)}" + ("  [unscrambled]" if is_034 else ""))
            self.lbl_loaded.setStyleSheet("color:#2dff6e; font-size:11px;")

            conf_c = {"HIGH": "#2dff6e", "MEDIUM": "#ff9900"}.get(result.confidence, "#ff6666")
            self.lbl_detected.setText(
                f"ECU: {result.version}  |  confidence: {result.confidence}  |  "
                f"CRC32: {crc:#010x}  |  checksum: {'✓ OK' if cs_ok else '⚠ invalid'}")
            self.lbl_detected.setStyleSheet(f"color:{conf_c}; font-size:11px;")

            # Auto-suggest injectors
            for rom in KNOWN_ROM_LIBRARY:
                if rom.version == result.version and rom.injectors != "STOCK_7A":
                    idx = self.cmb_inj_from.findData(rom.injectors)
                    if idx >= 0:
                        self.cmb_inj_from.setCurrentIndex(idx)
                    break

        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

        self._on_options_changed()

    def _on_options_changed(self, *_):
        do_disp = self.chk_disp.isChecked()
        do_inj  = self.chk_inj.isChecked()

        for w in (self.spn_disp_from, self.spn_disp_to, self.lbl_disp_scalar):
            w.setEnabled(do_disp)
        for w in (self.cmb_inj_from, self.cmb_inj_to, self.lbl_inj_scalar):
            w.setEnabled(do_inj)

        # Displacement scalar label
        if do_disp:
            fr = self.spn_disp_from.value()
            to = self.spn_disp_to.value()
            s  = to / fr if fr else 1.0
            self.lbl_disp_scalar.setText(f"  → fuel map {_s(s)}")
        else:
            self.lbl_disp_scalar.setText("")

        # Injector scalar label
        if do_inj:
            fp = INJECTOR_PROFILES.get(self.cmb_inj_from.currentData())
            tp = INJECTOR_PROFILES.get(self.cmb_inj_to.currentData())
            if fp and tp:
                s = fp.cc_at_4bar / tp.cc_at_4bar
                self.lbl_inj_scalar.setText(
                    f"  → fuel map {_s(s)}   "
                    f"({fp.cc_at_4bar:.0f} → {tp.cc_at_4bar:.0f} cc @ 4 bar)")
        else:
            self.lbl_inj_scalar.setText("")

        self._refresh_preview()

    def _combined_fuel_scalar(self) -> float:
        s = 1.0
        if self.chk_disp.isChecked():
            fr = self.spn_disp_from.value()
            to = self.spn_disp_to.value()
            if fr > 0:
                s *= to / fr
        if self.chk_inj.isChecked():
            fp = INJECTOR_PROFILES.get(self.cmb_inj_from.currentData())
            tp = INJECTOR_PROFILES.get(self.cmb_inj_to.currentData())
            if fp and tp:
                s *= fp.cc_at_4bar / tp.cc_at_4bar
        return s

    def _refresh_preview(self):
        do_disp = self.chk_disp.isChecked()
        do_inj  = self.chk_inj.isChecked()
        has_rom = self._rom_data is not None

        entry = BASE_ROMS.get(self._base_key, ("?", None, 2309, "STOCK_7A", ""))
        lines = [
            f"Base tune    :  {entry[0]}",
            f"ECU version  :  {self._ecu_version}",
            f"ROM loaded   :  {'Yes' if has_rom else '⚠  No ROM — select a base tune'}",
            "",
        ]

        if do_disp or do_inj:
            lines.append("Modifications:")
            if do_disp:
                fr = self.spn_disp_from.value()
                to = self.spn_disp_to.value()
                s  = to / fr if fr else 1.0
                lines.append(f"  Displacement  {fr} cc  →  {to} cc   fuel map {_s(s)}")
            if do_inj:
                fp = INJECTOR_PROFILES.get(self.cmb_inj_from.currentData())
                tp = INJECTOR_PROFILES.get(self.cmb_inj_to.currentData())
                if fp and tp:
                    s = fp.cc_at_4bar / tp.cc_at_4bar
                    lines.append(
                        f"  Injectors     {fp.cc_per_min} cc  →  {tp.cc_per_min} cc   "
                        f"fuel map {_s(s)}")

            combined = self._combined_fuel_scalar()
            lines.append(f"  Combined fuel map scalar:  {_s(combined)}")
        else:
            lines.append("No modifications selected  —  tick at least one box above.")

        self.txt_preview.setPlainText("\n".join(lines))

        can_build = has_rom and (do_disp or do_inj)
        self.btn_build.setEnabled(can_build)
        if not can_build:
            self.lbl_build_result.setText("")

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self):
        if not self._rom_data:
            QMessageBox.warning(self, "No ROM", "No base ROM loaded.")
            return

        do_disp = self.chk_disp.isChecked()
        do_inj  = self.chk_inj.isChecked()

        mdef = get_fuel_map_def(self._ecu_version)
        data = bytearray(
            self._rom_data[:65536] if len(self._rom_data) >= 65536
            else self._rom_data + bytes(65536 - len(self._rom_data)))

        log = []

        if do_disp:
            fr = self.spn_disp_from.value()
            to = self.spn_disp_to.value()
            if fr == to:
                QMessageBox.information(self, "No Change",
                                        "Displacement: from and to are the same.")
                return
            s    = to / fr
            fuel = list(data[mdef.data_addr:mdef.data_addr + mdef.size])
            data[mdef.data_addr:mdef.data_addr + mdef.size] = bytes(
                [max(0, min(255, round(v * s))) for v in fuel])
            log.append(f"Displacement  {fr} cc → {to} cc   fuel map {_s(s)}"
                       f"   ({mdef.size} cells)")

        if do_inj:
            fp = INJECTOR_PROFILES.get(self.cmb_inj_from.currentData())
            tp = INJECTOR_PROFILES.get(self.cmb_inj_to.currentData())
            if not fp or not tp:
                QMessageBox.warning(self, "Error", "Invalid injector selection.")
                return
            if fp == tp:
                QMessageBox.information(self, "No Change",
                                        "Injectors: from and to are the same.")
                return
            s    = fp.cc_at_4bar / tp.cc_at_4bar
            fuel = list(data[mdef.data_addr:mdef.data_addr + mdef.size])
            data[mdef.data_addr:mdef.data_addr + mdef.size] = bytes(
                [max(0, min(255, round(v * s))) for v in fuel])
            log.append(f"Injectors     {fp.cc_per_min} cc → {tp.cc_per_min} cc   "
                       f"fuel map {_s(s)}")

        # Mirror and fix checksum
        fixed32 = apply_checksum(bytearray(data[:32768]), self._ecu_version)
        data[:32768] = fixed32
        if len(data) == 65536:
            data[32768:] = fixed32

        # Default filename
        entry = BASE_ROMS.get(self._base_key, ("custom",))
        base_label = entry[0].split("(")[0].strip().replace(" ", "_")
        parts = []
        if do_disp:
            parts.append(f"{self.spn_disp_to.value()}cc")
        if do_inj:
            tp = INJECTOR_PROFILES.get(self.cmb_inj_to.currentData())
            if tp:
                parts.append(f"{tp.cc_per_min}inj")
        fname = f"{base_label}_{'_'.join(parts) if parts else 'scaled'}.bin"

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Built ROM", fname,
            "Binary ROM Files (*.bin);;All Files (*)")
        if not path:
            return

        try:
            with open(path, "wb") as f:
                f.write(bytes(data))
            summary = "  ·  ".join(log)
            self.lbl_build_result.setText(f"✓  {os.path.basename(path)}   —   {summary}")
            self.lbl_build_result.setStyleSheet("color:#2dff6e; font-size:11px;")
            QMessageBox.information(
                self, "Build Complete",
                f"Saved:  {os.path.basename(path)}\n\n"
                + "\n".join(f"  • {l}" for l in log)
                + "\n\nChecksum corrected automatically.")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    # ── Public API ────────────────────────────────────────────────────────────

    def set_teensy(self, teensy):
        self._teensy = teensy

    def load_rom_data(self, data: bytes, filepath: str = None):
        """Called from ROM Manager after Teensy download."""
        self._rom_data    = data
        self._ecu_version = detect_ecu_version(data[:32768]).version
        self._on_options_changed()

    def get_config(self) -> dict:
        return {
            "ecu_version":  self._ecu_version,
            "base_key":     self._base_key,
            "displacement": self.spn_disp_to.value(),
        }
