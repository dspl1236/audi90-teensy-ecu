"""
ui/hardware_config_tab.py
Hardware configuration — ECU version detection, MAF selection,
injector selection, and fuel map auto-scaling.
v1.2.0
"""

import os
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QPushButton, QLabel, QComboBox, QFileDialog,
    QMessageBox, QFrame, QSizePolicy, QSpinBox,
    QDoubleSpinBox, QCheckBox, QTextEdit, QGridLayout
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont

from ecu_profiles import (
    detect_ecu_version, scale_fuel_map, get_maf_scalar,
    hardware_summary, MAF_PROFILES, INJECTOR_PROFILES,
    DetectionResult, ECU_MAPS, KNOWN_ROM_LIBRARY,
    get_fuel_map_def, get_timing_map_def
)


CONFIDENCE_COLORS = {
    "HIGH":    "#2dff6e",
    "MEDIUM":  "#ff9900",
    "LOW":     "#ff3333",
    "UNKNOWN": "#ff3333",
}

ECU_DESCRIPTIONS = {
    "266B": "893 906 266 B  —  Early 7A  (2-connector, ~1988-89)\nMotronic 2.x  |  No VSS  |  No knock sensor on ECU connector",
    "266D": "893 906 266 D  —  Late 7A  (4-connector, ~1990-91)\nMotronic 2.x  |  VSS input  |  Knock  |  Extended I/O",
    "UNKNOWN": "Unknown — load a ROM file to detect version",
}


class HardwareConfigTab(QWidget):
    # Emitted when config changes — other tabs can react
    sig_config_changed = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._detection: DetectionResult = None
        self._rom_data:  bytes = None
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ── ECU Detection ──────────────────────────────────────────────────
        grp_ecu = QGroupBox("ECU Version Detection")
        ecu_lay = QVBoxLayout(grp_ecu)

        detect_row = QHBoxLayout()
        self.btn_load_rom  = QPushButton("📂  Load ROM for Detection")
        self.btn_detect    = QPushButton("🔍  Detect from Connected Teensy")
        self.btn_detect.setEnabled(False)
        self.btn_detect.setToolTip("Connect to Teensy first — will download active ROM and detect")
        self.btn_load_rom.clicked.connect(self._load_rom_for_detection)
        self.btn_detect.clicked.connect(self._detect_from_teensy)
        detect_row.addWidget(self.btn_load_rom)
        detect_row.addWidget(self.btn_detect)
        detect_row.addStretch()
        ecu_lay.addLayout(detect_row)

        # Detection result display
        result_grid = QGridLayout()
        result_grid.setColumnStretch(1, 1)

        def _lbl(text, style="color:#3d5068; font-size:11px;"):
            l = QLabel(text)
            l.setStyleSheet(style)
            return l

        result_grid.addWidget(_lbl("Version:"), 0, 0)
        self.lbl_version = QLabel("—")
        self.lbl_version.setStyleSheet("color:#bccdd8; font-size:14px; font-weight:bold;")
        result_grid.addWidget(self.lbl_version, 0, 1)

        result_grid.addWidget(_lbl("Confidence:"), 1, 0)
        self.lbl_confidence = QLabel("—")
        self.lbl_confidence.setStyleSheet("color:#3d5068;")
        result_grid.addWidget(self.lbl_confidence, 1, 1)

        result_grid.addWidget(_lbl("Method:"), 2, 0)
        self.lbl_method = QLabel("—")
        self.lbl_method.setStyleSheet("color:#3d5068; font-size:11px;")
        result_grid.addWidget(self.lbl_method, 2, 1)

        result_grid.addWidget(_lbl("ROM CRC32:"), 3, 0)
        self.lbl_crc = QLabel("—")
        self.lbl_crc.setStyleSheet("color:#3d5068; font-size:11px; font-family:monospace;")
        result_grid.addWidget(self.lbl_crc, 3, 1)

        result_grid.addWidget(_lbl("Description:"), 4, 0, Qt.AlignTop)
        self.lbl_desc = QLabel(ECU_DESCRIPTIONS["UNKNOWN"])
        self.lbl_desc.setStyleSheet("color:#bccdd8; font-size:11px;")
        self.lbl_desc.setWordWrap(True)
        result_grid.addWidget(self.lbl_desc, 4, 1)

        ecu_lay.addLayout(result_grid)

        # Warning box
        self.txt_warnings = QTextEdit()
        self.txt_warnings.setReadOnly(True)
        self.txt_warnings.setMaximumHeight(60)
        self.txt_warnings.setStyleSheet(
            "QTextEdit { background:#1a0a0a; border:1px solid #3d1010; "
            "color:#ff6666; font-size:11px; }"
        )
        self.txt_warnings.setVisible(False)
        ecu_lay.addWidget(self.txt_warnings)

        # ── Manual override ───────────────────────────────────────────────
        override_row = QHBoxLayout()
        override_row.addWidget(_lbl("Manual override:"))
        self.cmb_ecu_override = QComboBox()
        self.cmb_ecu_override.addItems(["Auto (from detection)", "266B — Early 7A", "266D — Late 7A"])
        self.cmb_ecu_override.setStyleSheet(
            "QComboBox { background:#0d1117; border:1px solid #1a2332; color:#bccdd8; padding:4px; }"
            "QComboBox::drop-down { border:none; }"
            "QComboBox QAbstractItemView { background:#0d1117; color:#bccdd8; }"
        )
        self.cmb_ecu_override.currentIndexChanged.connect(self._on_config_changed)
        override_row.addWidget(self.cmb_ecu_override)
        override_row.addStretch()
        ecu_lay.addLayout(override_row)

        # ── MAF Selection ──────────────────────────────────────────────────
        grp_maf = QGroupBox("MAF Sensor")
        maf_lay = QGridLayout(grp_maf)
        maf_lay.setColumnStretch(1, 1)

        maf_lay.addWidget(_lbl("Type:"), 0, 0)
        self.cmb_maf = QComboBox()
        for key, p in MAF_PROFILES.items():
            self.cmb_maf.addItem(p.display, key)
        self.cmb_maf.setStyleSheet(self.cmb_ecu_override.styleSheet())
        self.cmb_maf.currentIndexChanged.connect(self._on_maf_changed)
        maf_lay.addWidget(self.cmb_maf, 0, 1)

        maf_lay.addWidget(_lbl("Notes:"), 1, 0, Qt.AlignTop)
        self.lbl_maf_notes = QLabel(list(MAF_PROFILES.values())[0].notes)
        self.lbl_maf_notes.setStyleSheet("color:#3d5068; font-size:11px;")
        self.lbl_maf_notes.setWordWrap(True)
        maf_lay.addWidget(self.lbl_maf_notes, 1, 1)

        maf_lay.addWidget(_lbl("Displacement (cc):"), 2, 0)
        self.spn_displacement = QSpinBox()
        self.spn_displacement.setRange(2000, 3000)
        self.spn_displacement.setValue(2600)
        self.spn_displacement.setSuffix(" cc")
        self.spn_displacement.setStyleSheet(
            "QSpinBox { background:#0d1117; border:1px solid #1a2332; color:#bccdd8; padding:4px; }"
        )
        self.spn_displacement.valueChanged.connect(self._on_config_changed)
        maf_lay.addWidget(self.spn_displacement, 2, 1)

        maf_lay.addWidget(_lbl("MAF freq scalar:"), 3, 0)
        self.lbl_maf_scalar = QLabel("×1.130")
        self.lbl_maf_scalar.setStyleSheet("color:#00d4ff; font-size:12px; font-family:monospace;")
        maf_lay.addWidget(self.lbl_maf_scalar, 3, 1)

        # ── Injector Selection ────────────────────────────────────────────
        grp_inj = QGroupBox("Injectors")
        inj_lay = QGridLayout(grp_inj)
        inj_lay.setColumnStretch(1, 1)

        inj_lay.addWidget(_lbl("Size:"), 0, 0)
        self.cmb_injectors = QComboBox()
        for key, p in INJECTOR_PROFILES.items():
            self.cmb_injectors.addItem(f"{p.display}  ({p.cc_per_min}cc/min)", key)
        self.cmb_injectors.setStyleSheet(self.cmb_ecu_override.styleSheet())
        self.cmb_injectors.currentIndexChanged.connect(self._on_injector_changed)
        inj_lay.addWidget(self.cmb_injectors, 0, 1)

        inj_lay.addWidget(_lbl("Notes:"), 1, 0, Qt.AlignTop)
        self.lbl_inj_notes = QLabel(list(INJECTOR_PROFILES.values())[0].notes)
        self.lbl_inj_notes.setStyleSheet("color:#3d5068; font-size:11px;")
        self.lbl_inj_notes.setWordWrap(True)
        inj_lay.addWidget(self.lbl_inj_notes, 1, 1)

        inj_lay.addWidget(_lbl("Fuel map scalar:"), 2, 0)
        self.lbl_inj_scalar = QLabel("×1.000  (no change)")
        self.lbl_inj_scalar.setStyleSheet("color:#00d4ff; font-size:12px; font-family:monospace;")
        inj_lay.addWidget(self.lbl_inj_scalar, 2, 1)

        # ── Auto-scale button ─────────────────────────────────────────────
        grp_scale = QGroupBox("Auto-Scale Fuel Map")
        scale_lay = QVBoxLayout(grp_scale)

        scale_info = QLabel(
            "Load a base fuel map and rescale it for your injector size. "
            "The result is saved as a new .bin file ready to upload."
        )
        scale_info.setStyleSheet("color:#3d5068; font-size:11px;")
        scale_info.setWordWrap(True)
        scale_lay.addWidget(scale_info)

        from_to_row = QHBoxLayout()
        from_to_row.addWidget(_lbl("Base injectors:"))
        self.cmb_scale_from = QComboBox()
        for key, p in INJECTOR_PROFILES.items():
            self.cmb_scale_from.addItem(f"{p.display}  ({p.cc_per_min}cc)", key)
        self.cmb_scale_from.setStyleSheet(self.cmb_ecu_override.styleSheet())
        from_to_row.addWidget(self.cmb_scale_from)
        from_to_row.addWidget(_lbl("  →  target:"))
        self.cmb_scale_to = QComboBox()
        for key, p in INJECTOR_PROFILES.items():
            self.cmb_scale_to.addItem(f"{p.display}  ({p.cc_per_min}cc)", key)
        self.cmb_scale_to.setCurrentIndex(2)   # default: 550cc
        self.cmb_scale_to.setStyleSheet(self.cmb_ecu_override.styleSheet())
        from_to_row.addWidget(self.cmb_scale_to)
        from_to_row.addStretch()
        scale_lay.addLayout(from_to_row)

        self.btn_autoscale = QPushButton("⚡  Auto-Scale and Save New .bin")
        self.btn_autoscale.setEnabled(False)
        self.btn_autoscale.setToolTip("Load a ROM first")
        self.btn_autoscale.clicked.connect(self._auto_scale_fuel_map)
        scale_lay.addWidget(self.btn_autoscale)

        self.lbl_scale_result = QLabel("")
        self.lbl_scale_result.setStyleSheet("color:#3d5068; font-size:11px;")
        scale_lay.addWidget(self.lbl_scale_result)

        # ── Summary ───────────────────────────────────────────────────────
        self.lbl_summary = QLabel("Load a ROM and configure hardware to see summary")
        self.lbl_summary.setStyleSheet(
            "color:#3d5068; font-size:11px; padding:8px; "
            "border:1px solid #1a2332; background:#0d1117;"
        )
        self.lbl_summary.setWordWrap(True)

        # ── Assemble ──────────────────────────────────────────────────────
        top_row = QHBoxLayout()
        top_row.addWidget(grp_ecu, 2)

        right_col = QVBoxLayout()
        right_col.addWidget(grp_maf)
        right_col.addWidget(grp_inj)
        top_row.addLayout(right_col, 1)

        # ── Map Address Table ─────────────────────────────────────────────
        grp_maps = QGroupBox("Map Addresses  (from detected ECU version)")
        maps_lay = QVBoxLayout(grp_maps)

        maps_hdr = QHBoxLayout()
        self.lbl_map_version = QLabel("Load a ROM to see map addresses")
        self.lbl_map_version.setStyleSheet("color:#3d5068; font-size:11px;")
        maps_hdr.addWidget(self.lbl_map_version)
        maps_hdr.addStretch()
        self.btn_load_ecu_def = QPushButton("Load .ecu Definition")
        self.btn_load_ecu_def.setToolTip("Load a 034 .ecu file for TunerStudio-compatible map labels")
        self.btn_load_ecu_def.clicked.connect(self._load_ecu_definition)
        maps_hdr.addWidget(self.btn_load_ecu_def)
        maps_lay.addLayout(maps_hdr)

        self.tbl_maps = QTextEdit()
        self.tbl_maps.setReadOnly(True)
        self.tbl_maps.setMaximumHeight(145)
        self.tbl_maps.setFont(QFont("Courier New", 10))
        self.tbl_maps.setStyleSheet(
            "QTextEdit { background:#060a0f; border:1px solid #1a2332; "
            "color:#7a9ab0; font-family:'Courier New',monospace; font-size:11px; }"
        )
        self.tbl_maps.setPlainText("No ECU version detected yet.")
        maps_lay.addWidget(self.tbl_maps)

        root.addLayout(top_row)
        root.addWidget(grp_maps)
        root.addWidget(grp_scale)
        root.addWidget(self.lbl_summary)
        root.addStretch()

    # ── Detection ─────────────────────────────────────────────────────────────

    def _update_map_table(self, version: str):
        """Render the map address table for the given ECU version."""
        maps = ECU_MAPS.get(version)
        if not maps:
            self.tbl_maps.setPlainText("Unknown ECU version -- no map table available.")
            return

        self.lbl_map_version.setText(f"ECU {version}  --  {len(maps)} maps")
        lines = [f"  {'MAP NAME':<36} {'DATA':>6}  {'X-AXIS':>6}  {'Y-AXIS':>6}  {'SIZE':>5}  TYPE"]
        lines.append("  " + "-" * 75)
        for m in maps:
            x_str = f"0x{m.xaxis_addr:04X}" if m.xaxis_addr else "  --  "
            y_str = f"0x{m.yaxis_addr:04X}" if m.yaxis_addr else "  --  "
            if m.is_scalar:
                map_type = "scalar"
            elif m.is_2d:
                map_type = f"16x16"
            else:
                map_type = f"1x{m.cols}"
            lines.append(
                f"  {m.name:<36} 0x{m.data_addr:04X}  {x_str}  {y_str}  {m.size:>4}B  {map_type}"
            )
        self.tbl_maps.setPlainText("\n".join(lines))

    def _load_ecu_definition(self):
        """Load a .ecu TunerStudio definition file for display/reference."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load ECU Definition File", "",
            "ECU Definition Files (*.ecu);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
            import re
            strings = re.findall(b'[\x20-\x7e]{5,}', data)
            ecu_name = ""
            for s in strings:
                d = s.decode()
                if "266" in d or "Early" in d or "Late" in d or "7A" in d:
                    ecu_name = d
                    break
            ver = "266B" if "266B" in ecu_name or "Early" in ecu_name else "266D"
            QMessageBox.information(
                self, "ECU Definition Loaded",
                f"Loaded: {os.path.basename(path)}\nIdentified: {ecu_name}\nVersion: {ver}\n\n"
                f"Map addresses updated."
            )
            self._update_map_table(ver)
            if not self._detection:
                self.lbl_version.setText(ver)
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

    def _load_rom_for_detection(self):
        """Open a ROM file and run ECU version detection on it."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load ROM for ECU Detection", "",
            "ROM Files (*.034 *.bin *.ecu);;All Files (*)"
        )
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
            self._rom_data = data
            result = detect_ecu_version(data)
            self._apply_detection(result)
            self.btn_autoscale.setEnabled(True)
            self.btn_autoscale.setToolTip("")
        except Exception as e:
            QMessageBox.critical(self, "Load Error", str(e))

    def _detect_from_teensy(self):
        """Triggered when user wants to detect from connected Teensy."""
        # This gets called by main_window after downloading active ROM
        pass

    def load_rom_data(self, data: bytes, filepath: str = None):
        """Called externally (e.g. from ROM Manager after download)."""
        self._rom_data = data
        result = detect_ecu_version(data)
        self._apply_detection(result)
        self.btn_autoscale.setEnabled(True)

    def _apply_detection(self, result: DetectionResult):
        self._detection = result
        color = CONFIDENCE_COLORS.get(result.confidence, "#ff3333")

        self.lbl_version.setText(result.version)
        self.lbl_version.setStyleSheet(f"color:{color}; font-size:14px; font-weight:bold;")
        self.lbl_confidence.setText(f"{result.confidence}  —  {result.method}")
        self.lbl_confidence.setStyleSheet(f"color:{color}; font-size:11px;")
        self.lbl_method.setText(result.method)
        self.lbl_crc.setText(f"{result.crc32:#010x}")

        desc = ECU_DESCRIPTIONS.get(result.version, ECU_DESCRIPTIONS["UNKNOWN"])
        if result.cal_name:
            desc += f"\n\nKnown calibration: {result.cal_name}"
        if result.part_number:
            desc += f"  ({result.part_number})"
        self.lbl_desc.setText(desc)

        if result.warnings:
            self.txt_warnings.setVisible(True)
            self.txt_warnings.setPlainText("\n".join(result.warnings))
        else:
            self.txt_warnings.setVisible(False)

        # Auto-suggest hardware profile from known ROM library on CRC match
        if result.cal_name == "Stock":
            pass
        elif result.crc32:
            for rom in KNOWN_ROM_LIBRARY:
                if rom.version == result.version and rom.stage not in ("Stock",):
                    maf_idx = self.cmb_maf.findData(rom.maf)
                    inj_idx = self.cmb_injectors.findData(rom.injectors)
                    if maf_idx >= 0: self.cmb_maf.setCurrentIndex(maf_idx)
                    if inj_idx >= 0: self.cmb_injectors.setCurrentIndex(inj_idx)
                    break

        self._update_map_table(result.version)
        self._on_config_changed()

    def set_teensy(self, teensy):
        self._teensy = teensy
        self.btn_detect.setEnabled(teensy is not None)

    # ── Change handlers ───────────────────────────────────────────────────────

    def _on_maf_changed(self):
        key = self.cmb_maf.currentData()
        p   = MAF_PROFILES.get(key)
        if p:
            self.lbl_maf_notes.setText(p.notes)
            disp = self.spn_displacement.value()
            scalar = get_maf_scalar(key, disp / 2300.0)
            self.lbl_maf_scalar.setText(f"×{scalar:.3f}")
        self._on_config_changed()

    def _on_injector_changed(self):
        key = self.cmb_injectors.currentData()
        p   = INJECTOR_PROFILES.get(key)
        if p:
            self.lbl_inj_notes.setText(p.notes)
            self.lbl_inj_scalar.setText(
                f"×{p.scalar:.3f}  ({'no change' if p.scalar == 1.0 else 'rescale fuel map'})"
            )
        self._on_config_changed()

    def _on_config_changed(self):
        maf  = self.cmb_maf.currentData()
        inj  = self.cmb_injectors.currentData()
        disp = self.spn_displacement.value()
        ver  = self.get_ecu_version()

        self.lbl_summary.setText(
            f"ECU: {ver}   |   {hardware_summary(maf, inj, disp)}"
        )
        self.lbl_summary.setStyleSheet(
            "color:#bccdd8; font-size:11px; padding:8px; "
            "border:1px solid #1a2332; background:#0d1117;"
        )

        self.sig_config_changed.emit(self.get_config())

    # ── Auto-scale ────────────────────────────────────────────────────────────

    def _auto_scale_fuel_map(self):
        if not self._rom_data:
            QMessageBox.warning(self, "No ROM", "Load a ROM file first.")
            return

        from_key = self.cmb_scale_from.currentData()
        to_key   = self.cmb_scale_to.currentData()

        if from_key == to_key:
            QMessageBox.information(self, "No Change", "Source and target injectors are the same.")
            return

        from_p = INJECTOR_PROFILES[from_key]
        to_p   = INJECTOR_PROFILES[to_key]

        # Get map addresses for detected ECU version
        ver   = self.get_ecu_version()
        mdef  = get_fuel_map_def(ver)

        data = bytearray(self._rom_data)
        fuel_base = list(data[mdef.data_addr:mdef.data_addr + mdef.size])
        scaled    = scale_fuel_map(fuel_base, from_key, to_key)

        # Write scaled map back into ROM
        for i, v in enumerate(scaled):
            data[mdef.data_addr + i] = v
        # Mirror if ROM is mirrored
        if len(data) == 65536:
            data[0x8000 + mdef.data_addr:0x8000 + mdef.data_addr + mdef.size] = bytes(scaled)

        factor = from_p.cc_per_min / to_p.cc_per_min
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Scaled ROM",
            f"tune_{to_p.cc_per_min}cc_scaled.bin",
            "Binary Files (*.bin);;All Files (*)"
        )
        if path:
            try:
                with open(path, "wb") as f:
                    f.write(bytes(data))
                self.lbl_scale_result.setText(
                    f"Saved  {os.path.basename(path)}  --  "
                    f"{from_p.cc_per_min}cc -> {to_p.cc_per_min}cc  "
                    f"(x{factor:.3f} applied to {mdef.size} fuel cells)"
                )
                self.lbl_scale_result.setStyleSheet("color:#2dff6e; font-size:11px;")
            except Exception as e:
                QMessageBox.critical(self, "Save Error", str(e))

    # ── Public API ────────────────────────────────────────────────────────────

    def get_ecu_version(self) -> str:
        """Get effective ECU version (manual override or detected)."""
        idx = self.cmb_ecu_override.currentIndex()
        if idx == 1:
            return "266B"
        elif idx == 2:
            return "266D"
        return self._detection.version if self._detection else "266D"

    def get_config(self) -> dict:
        return {
            "ecu_version":   self.get_ecu_version(),
            "maf":           self.cmb_maf.currentData(),
            "injectors":     self.cmb_injectors.currentData(),
            "displacement":  self.spn_displacement.value(),
            "maf_scalar":    get_maf_scalar(
                                self.cmb_maf.currentData(),
                                self.spn_displacement.value() / 2300.0
                             ),
        }
