"""
ui/rom_manager_tab.py
ROM Manager — Download from Teensy SD, Upload to Teensy SD,
Offline map editing, corrections toggle.
v1.4.0
"""

import os
import struct
import zlib
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QPushButton, QLabel, QListWidget, QProgressBar,
    QFileDialog, QMessageBox, QSplitter, QFrame,
    QSizePolicy, QTabWidget
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QColor, QBrush

# Reuse the map table widget from map editor
from ui.map_editor_tab import MapTable, ROWS, COLS
from ecu_profiles import (
    unscramble_rom, raw_to_display, display_to_raw,
    raw_to_lambda, lambda_to_raw,
    read_rpm_axis_from_rom, read_load_axis_from_rom,
    read_fuel_rpm_axis, read_timing_rpm_axis, read_load_axis,
    RPM_AXIS_266D, RPM_AXIS_266B, TIMING_RPM_AXIS, LOAD_AXIS_266D,
    FUEL_DATA_FACTOR, FUEL_DATA_OFFSET, FUEL_DATA_SIGNED,
    apply_checksum, verify_checksum,
    detect_ecu_version, KNOWN_ROM_LIBRARY, ECU_MAPS,
)

FUEL_MAP_ADDR   = 0x0000   # 266D Primary Fueling  — 16×16 = 256 bytes (native ROM space)
TIMING_MAP_ADDR = 0x0100   # 266D Primary Timing   — 16×16 = 256 bytes (native ROM space)
ROM_SIZE        = 65536
MAP_SIZE        = ROWS * COLS   # 256 bytes per map (16 RPM rows × 16 Load cols)


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


# ── Worker thread for download ────────────────────────────────────────────────

class DownloadWorker(QThread):
    progress  = pyqtSignal(int, int)     # bytes_done, total
    complete  = pyqtSignal(bytes, str)   # data, filename
    error     = pyqtSignal(str)

    def __init__(self, teensy, filename):
        super().__init__()
        self.teensy   = teensy
        self.filename = filename
        self._chunks  = {}
        self._total   = 0
        self._done    = False
        self._error   = None

    def run(self):
        import time
        # Register ourselves as the active xfer handler
        self.teensy._xfer_handler = self
        self.teensy.send_command(f"CMD:ROM_DOWNLOAD,{self.filename}")

        # Wait for completion (max 30s)
        timeout = 30.0
        elapsed = 0.0
        while not self._done and elapsed < timeout:
            time.sleep(0.1)
            elapsed += 0.1

        self.teensy._xfer_handler = None

        if self._error:
            self.error.emit(self._error)
        elif not self._done:
            self.error.emit("Download timed out")

    def feed_xfer_start(self, filename, size):
        self._total = size
        self.progress.emit(0, size)

    def feed_xfer_chunk(self, idx, data):
        self._chunks[idx] = data
        done = sum(len(c) for c in self._chunks.values())
        self.progress.emit(done, self._total)

    def feed_xfer_end(self, expected_crc):
        data = bytearray(self._total)
        for idx, chunk in sorted(self._chunks.items()):
            offset = idx * 256
            data[offset:offset + len(chunk)] = chunk
        actual = crc32(bytes(data))
        if actual == expected_crc:
            self.complete.emit(bytes(data), self.filename)
            self._done = True
        else:
            self._error = f"CRC mismatch — expected {expected_crc:#010x}, got {actual:#010x}"
            self._done  = True

    def feed_error(self, msg):
        self._error = msg
        self._done  = True


# ── Worker thread for upload ──────────────────────────────────────────────────

class UploadWorker(QThread):
    progress  = pyqtSignal(int, int)
    complete  = pyqtSignal(str)
    error     = pyqtSignal(str)

    CHUNK_SIZE = 256

    def __init__(self, teensy, filename, data):
        super().__init__()
        self.teensy   = teensy
        self.filename = filename
        self.data     = data
        self._ready   = False
        self._done    = False
        self._error   = None
        self._success = False

    def run(self):
        import time
        self.teensy._xfer_handler = self
        self.teensy.send_command(
            f"CMD:ROM_UPLOAD,{self.filename},{len(self.data)}"
        )

        # Wait for ready ACK (max 10s)
        for _ in range(100):
            if self._ready or self._error:
                break
            time.sleep(0.1)

        if self._error:
            self.error.emit(self._error)
            self.teensy._xfer_handler = None
            return

        if not self._ready:
            self.error.emit("Teensy did not respond to upload request")
            self.teensy._xfer_handler = None
            return

        # Send chunks
        total  = len(self.data)
        offset = 0
        idx    = 0
        try:
            while offset < total:
                chunk = self.data[offset:offset + self.CHUNK_SIZE]
                header = f"XFER:CHUNK,{idx},{len(chunk)}\n".encode("ascii")
                with self.teensy._lock:
                    self.teensy._serial.write(header)
                    self.teensy._serial.write(chunk)
                    self.teensy._serial.write(b"\n")
                    self.teensy._serial.flush()
                offset += len(chunk)
                idx    += 1
                self.progress.emit(offset, total)
                time.sleep(0.008)

            # Send CRC
            self.teensy.send_command(f"XFER:DONE,{crc32(self.data)}")
        except Exception as e:
            self.error.emit(str(e))
            self.teensy._xfer_handler = None
            return

        # Wait for complete ACK (max 15s)
        for _ in range(150):
            if self._done:
                break
            time.sleep(0.1)

        self.teensy._xfer_handler = None

        if self._success:
            self.complete.emit(self.filename)
        elif self._error:
            self.error.emit(self._error)
        else:
            self.error.emit("Upload timed out waiting for confirmation")

    def feed_ready(self):
        self._ready = True

    def feed_complete(self):
        self._success = True
        self._done    = True

    def feed_error(self, msg):
        self._error = msg
        self._done  = True


# ── Offline ROM editor (no connection needed) ─────────────────────────────────

class _ScalarEdit(QWidget):
    """Simple spin-like row for editing a single integer scalar value."""
    from PyQt5.QtCore import pyqtSignal as _sig
    value_changed = _sig()

    def __init__(self, parent=None):
        from PyQt5.QtWidgets import QHBoxLayout, QSpinBox
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._spin = QSpinBox()
        self._spin.setRange(0, 65535)
        self._spin.setFixedWidth(90)
        self._spin.setStyleSheet(
            "QSpinBox { background:#1a2530; color:#e0eaf4; border:1px solid #2a3a4a; "
            "padding:2px 4px; font-size:12px; }"
        )
        self._spin.valueChanged.connect(lambda _: self.value_changed.emit())
        lay.addWidget(self._spin)

    def set_value(self, v): self._spin.setValue(int(v))
    def get_value(self): return self._spin.value()


def _separator():
    from PyQt5.QtWidgets import QFrame
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color: #2a3a4a;")
    return f


class _OneDTable(QWidget):
    """Compact 1-row or 1-column table for 1-D maps and MAF linearization."""
    from PyQt5.QtCore import pyqtSignal as _sig
    value_changed = _sig()

    def __init__(self, count: int, sixteen_bit: bool = False, parent=None):
        from PyQt5.QtWidgets import QHBoxLayout, QScrollArea, QLineEdit, QSizePolicy
        super().__init__(parent)
        self._count = count
        self._cells = []
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedHeight(52)

        inner = QWidget()
        row = QHBoxLayout(inner)
        row.setContentsMargins(2, 2, 2, 2)
        row.setSpacing(2)

        max_val = 65535 if sixteen_bit else 255
        for _ in range(count):
            cell = QLineEdit("0")
            cell.setFixedWidth(52 if sixteen_bit else 38)
            cell.setFixedHeight(30)
            cell.setAlignment(Qt.AlignCenter)
            cell.setStyleSheet(
                "QLineEdit { background:#1a2530; color:#e0eaf4; "
                "border:1px solid #2a3a4a; font-size:10px; }"
            )
            cell.textChanged.connect(lambda _: self.value_changed.emit())
            row.addWidget(cell)
            self._cells.append(cell)
        row.addStretch()

        scroll.setWidget(inner)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll)

    def load_values(self, values):
        for i, v in enumerate(values[:self._count]):
            self._cells[i].blockSignals(True)
            self._cells[i].setText(str(v))
            self._cells[i].blockSignals(False)

    def get_values(self):
        result = []
        for cell in self._cells:
            try:
                result.append(float(cell.text()))
            except ValueError:
                result.append(0)
        return result


class OfflineRomEditor(QWidget):
    """
    Load a .bin / .034 file, view and edit all ECU maps, save as new .bin.

    Tabs (version-aware):
      Both:  Fuel Map  |  Timing Map  |  Knock Timing  |  Scalars & 1-D
      266B only:  MAF Linearization
    Save philosophy: Save As only — never silently overwrite the source file.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._filepath    = None
        self._save_path   = None
        self._romdata     = bytearray(ROM_SIZE)
        self._dirty       = False
        self._ecu_version = "266D"
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # Toolbar
        toolbar = QHBoxLayout()
        self.btn_open   = QPushButton("📂  Open .bin")
        self.btn_saveas = QPushButton("💾  Save As .bin...")
        self.lbl_file   = QLabel("No file loaded  —  open a .bin or download from Teensy")
        self.lbl_file.setStyleSheet("color: #3d5068; font-size: 11px;")
        self.lbl_dirty  = QLabel("")
        self.lbl_dirty.setStyleSheet("color: #ff9900; font-size: 11px;")
        self.btn_saveas.setEnabled(False)
        self.btn_open.clicked.connect(self._open_file)
        self.btn_saveas.clicked.connect(self._save_as_file)
        toolbar.addWidget(self.btn_open)
        toolbar.addWidget(self.btn_saveas)
        toolbar.addSpacing(12)
        toolbar.addWidget(self.lbl_file)
        toolbar.addWidget(self.lbl_dirty)
        toolbar.addStretch()

        # Map tabs
        self.map_tabs = QTabWidget()

        # Tab 0: Fuel Map
        fuel_widget = QWidget()
        fl = QVBoxLayout(fuel_widget); fl.setContentsMargins(0,0,0,0)
        self.lbl_fuel_info = QLabel("", styleSheet="color:#3d5068; font-size:11px; padding:4px 0;")
        fl.addWidget(self.lbl_fuel_info)
        self.fuel_table = MapTable("fuel")
        self.fuel_table._teensy = None
        self.fuel_table.itemChanged.connect(self._on_edit)
        fl.addWidget(self.fuel_table)
        self.map_tabs.addTab(fuel_widget, "Fuel Map")

        # Tab 1: Timing Map
        timing_widget = QWidget()
        tl = QVBoxLayout(timing_widget); tl.setContentsMargins(0,0,0,0)
        tl.addWidget(QLabel("Timing Map — 16×16  |  Rows=RPM  |  Cols=Load kPa  |  display=raw° BTDC  |  "
                            "values >128 = retard (e.g. 251 = −5°)  |  Hover cell for signed °",
                            styleSheet="color:#3d5068; font-size:11px; padding:4px 0;"))
        self.timing_table = MapTable("timing")
        self.timing_table._teensy = None
        self.timing_table.itemChanged.connect(self._on_edit)
        tl.addWidget(self.timing_table)
        self.map_tabs.addTab(timing_widget, "Timing Map")

        # Tab 2: Knock Timing
        knock_widget = QWidget()
        kl = QVBoxLayout(knock_widget); kl.setContentsMargins(0,0,0,0)
        kl.addWidget(QLabel(
            "Knock Safety Timing — 16×16  |  Rows=RPM  |  Cols=Load kPa  |  display=raw° BTDC\n"
            "ECU switches to this map on knock detection.  Values >128 = retard.  Hover cell for signed °.",
            styleSheet="color:#3d5068; font-size:11px; padding:4px 0;"))
        self.knock_table = MapTable("timing")
        self.knock_table._teensy = None
        self.knock_table.itemChanged.connect(self._on_edit)
        kl.addWidget(self.knock_table)
        self.map_tabs.addTab(knock_widget, "Knock Timing")

        # Tab 3: Scalars & 1-D
        scalars_widget = QWidget()
        sl = QVBoxLayout(scalars_widget)
        sl.setContentsMargins(8,8,8,8); sl.setSpacing(10)
        sl.addWidget(QLabel("Scalar Values  —  single-byte parameters",
                    styleSheet="color:#aabbcc; font-size:12px; font-weight:bold; padding:4px 0;"))

        inj_row = QHBoxLayout()
        self.lbl_inj_info = QLabel("", styleSheet="color:#3d5068; font-size:10px;")
        inj_row.addWidget(QLabel("Injection Scaler  (raw byte):", styleSheet="color:#aabbcc; min-width:200px;"))
        self.spin_inj = _ScalarEdit()
        self.spin_inj.setToolTip(
            "Injection Scaler — raw byte stored at 0x077E\n"
            "display = raw × 0.3922\n"
            "Larger injectors → smaller value\n"
            "Larger MAF sensor → larger value")
        self.spin_inj.value_changed.connect(self._on_edit)
        inj_row.addWidget(self.spin_inj)
        inj_row.addWidget(self.lbl_inj_info)
        inj_row.addStretch()
        sl.addLayout(inj_row)

        cl_row = QHBoxLayout()
        cl_row.addWidget(QLabel("CL Disable RPM  (×25):", styleSheet="color:#aabbcc; min-width:200px;"))
        self.spin_cl_rpm = _ScalarEdit()
        self.spin_cl_rpm.setToolTip(
            "Disable O2 closed-loop above this RPM\n"
            "Stored as raw × 25  →  enter the RPM value directly")
        self.spin_cl_rpm.value_changed.connect(self._on_edit)
        cl_row.addWidget(self.spin_cl_rpm)
        cl_row.addWidget(QLabel("RPM", styleSheet="color:#3d5068;"))
        cl_row.addStretch()
        sl.addLayout(cl_row)

        sl.addSpacing(8)
        sl.addWidget(_separator())
        sl.addWidget(QLabel(
            "Decel Fuel Cutoff — 1×16  |  Axis=RPM  |  Values=Load threshold kPa\n"
            "Injectors cut below this load during deceleration.",
            styleSheet="color:#3d5068; font-size:11px; padding:4px 0;"))
        self.decel_table = _OneDTable(16)
        self.decel_table.value_changed.connect(self._on_edit)
        sl.addWidget(self.decel_table)

        sl.addSpacing(8)
        sl.addWidget(QLabel(
            "Closed-Loop Load Limit — 1×16  |  Axis=RPM  |  O2 feedback disabled above this load.",
            styleSheet="color:#3d5068; font-size:11px; padding:4px 0;"))
        self.cl_load_table = _OneDTable(16)
        self.cl_load_table.value_changed.connect(self._on_edit)
        sl.addWidget(self.cl_load_table)
        sl.addStretch()
        self.map_tabs.addTab(scalars_widget, "Scalars & 1-D")

        # Tab 4: MAF Linearization (266B and 266D — same address 0x02D0)
        self.maf_widget = QWidget()
        ml = QVBoxLayout(self.maf_widget); ml.setContentsMargins(0,0,0,0)
        ml.addWidget(QLabel(
            "MAF Linearization — 1×64  |  16-bit big-endian values  |  266B + 266D\n"
            "Maps MAF sensor frequency → load signal.  "
            "034 RIP Chip only exposes this for 266B but 266D ROM contains identical table at same address.\n"
            "Different MAF housings (Coupe vs Sedan) or sensor swaps may require recalibration here.",
            styleSheet="color:#3d5068; font-size:11px; padding:4px 0;"))
        self.maf_table = _OneDTable(64, sixteen_bit=True)
        self.maf_table.value_changed.connect(self._on_edit)
        ml.addWidget(self.maf_table)
        self.maf_tab_idx = self.map_tabs.addTab(self.maf_widget, "MAF Lin")

        # Tab 5: Warmup / Idle Enrichment  (HIGH confidence — axis+data pattern confirmed)
        self.warmup_widget = QWidget()
        wl = QVBoxLayout(self.warmup_widget); wl.setContentsMargins(0,0,0,0)
        wl.addWidget(QLabel(
            "Warmup / Idle Enrichment — 1×16  |  Axis=RPM (250–4000)  |  @ 0x06C0 / 0x06D0\n"
            "Fuel enrichment vs RPM at idle/warmup. 200=richest (cold idle), tapers to 98 at higher RPM.\n"
            "Identical in 266B and 266D. Affects cold start behaviour and idle quality.",
            styleSheet="color:#3d5068; font-size:11px; padding:4px 0;"))
        WARMUP_AXIS = [v*25 for v in [10,20,30,40,50,60,70,80,90,100,110,120,130,140,150,160]]
        wl.addWidget(QLabel("RPM axis: " + "  ".join(str(v) for v in WARMUP_AXIS),
                            styleSheet="color:#5a7a90; font-size:10px; font-family:monospace; padding:2px 0;"))
        self.warmup_table = _OneDTable(16)
        self.warmup_table.value_changed.connect(self._on_edit)
        wl.addWidget(self.warmup_table)
        wl.addStretch()
        self.warmup_tab_idx = self.map_tabs.addTab(self.warmup_widget, "Warmup Enrich")

        # Tab 6: Extra Decel Map  (HIGH confidence — immediately follows documented decel)
        self.decel2_widget = QWidget()
        d2l = QVBoxLayout(self.decel2_widget); d2l.setContentsMargins(0,0,0,0)
        d2l.addWidget(QLabel(
            "Extra Decel Maps — 2×8  |  @ 0x0E40  |  raw values (×0.3922 = kPa approx)\n"
            "Two 8-entry threshold tables immediately after documented decel cutoff.\n"
            "Likely separate decel fuel-cut thresholds for different conditions (e.g. hot/cold or partial/closed throttle).\n"
            "⚠ Purpose inferred — not documented by 034. Edit with caution.",
            styleSheet="color:#7a6030; font-size:11px; padding:4px 0;"))
        d2l.addWidget(QLabel("Row A (0x0E40, 8 entries):",
                             styleSheet="color:#5a7a90; font-size:10px; padding:2px 0;"))
        self.decel2a_table = _OneDTable(8)
        self.decel2a_table.value_changed.connect(self._on_edit)
        d2l.addWidget(self.decel2a_table)
        d2l.addWidget(QLabel("Row B (0x0E48, 8 entries):",
                             styleSheet="color:#5a7a90; font-size:10px; padding:2px 0;"))
        self.decel2b_table = _OneDTable(8)
        self.decel2b_table.value_changed.connect(self._on_edit)
        d2l.addWidget(self.decel2b_table)
        d2l.addStretch()
        self.decel2_tab_idx = self.map_tabs.addTab(self.decel2_widget, "Decel Extra")

        # Tab 7: Overrun Curves  (MEDIUM confidence)
        self.overrun_widget = QWidget()
        ol = QVBoxLayout(self.overrun_widget); ol.setContentsMargins(0,0,0,0)
        ol.addWidget(QLabel(
            "Overrun / Fuel-Cut Curves — 4×16  |  @ 0x0550–0x0580\n"
            "Four 16-entry tables with sharply decreasing values (255→12).\n"
            "Likely overrun fuel-cut ramp curves vs load — each row may correspond to a different RPM band.\n"
            "⚠ Purpose inferred from data shape — not documented by 034.",
            styleSheet="color:#7a6030; font-size:11px; padding:4px 0;"))
        self.overrun_tables = []
        labels = ["Row A (0x0550)", "Row B (0x0560)", "Row C (0x0570)", "Row D (0x0580)"]
        for lbl in labels:
            ol.addWidget(QLabel(lbl, styleSheet="color:#5a7a90; font-size:10px; padding:2px 0;"))
            t = _OneDTable(16)
            t.value_changed.connect(self._on_edit)
            ol.addWidget(t)
            self.overrun_tables.append(t)
        ol.addStretch()
        self.overrun_tab_idx = self.map_tabs.addTab(self.overrun_widget, "Overrun Curves")

        # Tab 8: Sensor / Calibration tables  (MEDIUM confidence, 266D only)
        self.sensor_widget = QWidget()
        senl = QVBoxLayout(self.sensor_widget); senl.setContentsMargins(0,0,0,0)
        senl.addWidget(QLabel(
            "Sensor Calibration Tables — 2×16  |  @ 0x1120 / 0x1130  |  266D only (not in 266B)\n"
            "Two evenly-stepped 16-point lookup tables (delta ~15–16 per step, range 8–227).\n"
            "Likely O2 sensor gain/linearization or coolant temp correction.\n"
            "⚠ Purpose inferred — not documented by 034. 266B does not have these.",
            styleSheet="color:#7a6030; font-size:11px; padding:4px 0;"))
        senl.addWidget(QLabel("Table A (0x1120):", styleSheet="color:#5a7a90; font-size:10px; padding:2px 0;"))
        self.sensor_a_table = _OneDTable(16)
        self.sensor_a_table.value_changed.connect(self._on_edit)
        senl.addWidget(self.sensor_a_table)
        senl.addWidget(QLabel("Table B (0x1130):", styleSheet="color:#5a7a90; font-size:10px; padding:2px 0;"))
        self.sensor_b_table = _OneDTable(16)
        self.sensor_b_table.value_changed.connect(self._on_edit)
        senl.addWidget(self.sensor_b_table)
        senl.addStretch()
        self.sensor_tab_idx = self.map_tabs.addTab(self.sensor_widget, "Sensor Cal")

        # ── ECU detection info strip ──────────────────────────────────────
        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 2, 0, 2)

        self.lbl_ecu_version = QLabel("ECU: —")
        self.lbl_ecu_version.setStyleSheet(
            "color:#2dff6e; font-size:12px; font-weight:bold; padding:2px 8px 2px 0;")
        self.lbl_ecu_confidence = QLabel("")
        self.lbl_ecu_confidence.setStyleSheet(
            "color:#3d5068; font-size:11px; padding:2px 8px 2px 0;")
        self.lbl_ecu_crc = QLabel("")
        self.lbl_ecu_crc.setStyleSheet(
            "color:#3d5068; font-size:11px; font-family:monospace; padding:2px 8px 2px 0;")
        self.lbl_ecu_cal = QLabel("")
        self.lbl_ecu_cal.setStyleSheet(
            "color:#7a9ab0; font-size:11px; padding:2px 8px 2px 0;")

        self.btn_map_addrs = QPushButton("Map Addresses ▼")
        self.btn_map_addrs.setFlat(True)
        self.btn_map_addrs.setStyleSheet(
            "QPushButton { color:#3d5068; font-size:10px; border:none; padding:0 4px; }"
            "QPushButton:hover { color:#7a9ab0; }")
        self.btn_map_addrs.setCheckable(True)
        self.btn_map_addrs.setChecked(False)
        self.btn_map_addrs.toggled.connect(self._toggle_map_addrs)

        info_row.addWidget(self.lbl_ecu_version)
        info_row.addWidget(self.lbl_ecu_confidence)
        info_row.addWidget(self.lbl_ecu_crc)
        info_row.addWidget(self.lbl_ecu_cal)
        info_row.addStretch()
        info_row.addWidget(self.btn_map_addrs)

        from PyQt5.QtWidgets import QTextEdit as _QTE
        from PyQt5.QtGui import QFont as _QFont
        self.wgt_map_addrs = _QTE()
        self.wgt_map_addrs.setReadOnly(True)
        self.wgt_map_addrs.setMaximumHeight(130)
        self.wgt_map_addrs.setFont(_QFont("Courier New", 10))
        self.wgt_map_addrs.setStyleSheet(
            "QTextEdit { background:#060a0f; border:1px solid #1a2332; "
            "color:#7a9ab0; font-family:'Courier New',monospace; font-size:10px; }")
        self.wgt_map_addrs.setVisible(False)

        root.addLayout(toolbar)
        root.addLayout(info_row)
        root.addWidget(self.wgt_map_addrs)
        root.addWidget(self.map_tabs, 1)

    # File I/O

    def load_data(self, data: bytes, filepath: str = None):
        is_034 = filepath and filepath.lower().endswith('.034')
        if is_034:
            data = unscramble_rom(data)
        if len(data) < ROM_SIZE:
            data = data + bytes(ROM_SIZE - len(data))
        self._romdata  = bytearray(data[:ROM_SIZE])
        self._filepath = filepath
        self._save_path = None
        self._dirty    = False
        self._is_034   = is_034

        result = detect_ecu_version(self._romdata[:32768])
        self._ecu_version = result.version
        self._update_ecu_info(result)
        is_266b = (self._ecu_version == "266B")
        is_aah  = (self._ecu_version == "AAH")
        rom = self._romdata

        # Fuel Map
        raw_fuel = list(rom[0x0000:0x0000 + MAP_SIZE])
        if is_266b:
            fuel_display = [raw_to_lambda(b) for b in raw_fuel]
            self.lbl_fuel_info.setText(
                "Fuel Map (Lambda) — 16×16  |  Rows=RPM  |  Cols=Load kPa  |  "
                "display=signed×0.007813+1.0  |  1.000=stoich  |  stock: 0.625–0.867")
        elif is_aah:
            fuel_display = [raw_to_lambda(b) for b in raw_fuel]
            self.lbl_fuel_info.setText(
                "Fuel Map (V6 AAH) — 16×16  |  Rows=RPM  |  Cols=Load kPa  |  "
                "display=signed×0.007813+1.0  (Lambda)  |  1.000=stoich  |  stock Stage1: 0.539–1.414  |  RPM axis 500–6000")
        else:
            fuel_display = [raw_to_display(b) for b in raw_fuel]
            self.lbl_fuel_info.setText(
                "Fuel Map — 16×16  |  Rows=RPM  |  Cols=Load kPa  |  "
                "display=signed+128  |  stock: 40–123")
        fuel_rpm  = read_fuel_rpm_axis(bytes(rom[:32768]), self._ecu_version)
        timing_rpm = read_timing_rpm_axis(bytes(rom[:32768]), self._ecu_version)
        load_kpa  = read_load_axis(bytes(rom[:32768]), self._ecu_version)

        self.fuel_table.load_data([round(v, 3) if (is_266b or is_aah) else int(v) for v in fuel_display])
        self.fuel_table.set_axis_labels(fuel_rpm, load_kpa)

        # Timing Map
        self.timing_table.load_data(list(rom[0x0100:0x0100 + MAP_SIZE]))
        self.timing_table.set_axis_labels(timing_rpm, load_kpa)

        # Knock Timing
        self.knock_table.load_data(list(rom[0x1000:0x1000 + MAP_SIZE]))
        self.knock_table.set_axis_labels(timing_rpm, load_kpa)

        # Injection Scaler
        INJ_ADDR = 0x077E
        inj_raw  = rom[INJ_ADDR]
        self.spin_inj.set_value(inj_raw)
        self.lbl_inj_info.setText(f"raw={inj_raw}  →  {inj_raw*0.3922:.2f}  (×0.3922)   addr=0x{INJ_ADDR:04X}")

        # CL Disable RPM
        cl_rpm_raw = rom[0x07E1]
        self.spin_cl_rpm.set_value(cl_rpm_raw * 25)

        # Decel Cutoff
        DECEL_ADDR = 0x0E30
        self.decel_table.load_values([round(rom[DECEL_ADDR+i]*0.3922,1) for i in range(16)])

        # CL Load Limit
        CL_LOAD_ADDR = 0x0660
        self.cl_load_table.load_values(list(rom[CL_LOAD_ADDR:CL_LOAD_ADDR+16]))

        # MAF tab — visible for 266B and 266D (same address, same format)
        self.map_tabs.setTabVisible(self.maf_tab_idx, not is_aah)
        if not is_aah:
            MAF_ADDR = 0x02D0
            maf_vals = [int.from_bytes(rom[MAF_ADDR+i*2:MAF_ADDR+i*2+2], 'big') for i in range(64)]
            self.maf_table.load_values(maf_vals)

        # Warmup Enrichment (0x06D0) — all 266x ECUs
        self.map_tabs.setTabVisible(self.warmup_tab_idx, not is_aah)
        if not is_aah:
            self.warmup_table.load_values(list(rom[0x06D0:0x06D0+16]))

        # Extra Decel Map (0x0E40) — all ECUs
        self.decel2a_table.load_values(list(rom[0x0E40:0x0E48]))
        self.decel2b_table.load_values(list(rom[0x0E48:0x0E50]))

        # Overrun Curves (0x0550-0x0580) — all 266x ECUs
        self.map_tabs.setTabVisible(self.overrun_tab_idx, not is_aah)
        if not is_aah:
            for i, t in enumerate(self.overrun_tables):
                t.load_values(list(rom[0x0550+i*16:0x0560+i*16]))

        # Sensor Calibration tables (0x1120/0x1130) — 266D only
        self.map_tabs.setTabVisible(self.sensor_tab_idx, self._ecu_version == "266D")
        if self._ecu_version == "266D":
            self.sensor_a_table.load_values(list(rom[0x1120:0x1130]))
            self.sensor_b_table.load_values(list(rom[0x1130:0x1140]))

        # Status
        cs_ok  = verify_checksum(bytes(rom[:32768]), self._ecu_version)
        cs_str = "✓ checksum OK" if cs_ok else "⚠ checksum INVALID"
        name   = os.path.basename(filepath) if filepath else "downloaded ROM"
        suffix = "  [.034 unscrambled]" if is_034 else ""
        self.lbl_file.setText(f"  {name}{suffix}  —  {self._ecu_version}  —  {cs_str}")
        self.lbl_file.setStyleSheet(
            "color: #2dff6e; font-size: 11px;" if cs_ok else "color: #ff6e2d; font-size: 11px;")
        self.lbl_dirty.setText("")
        self.btn_saveas.setEnabled(True)

    def get_data(self) -> bytes:
        is_266b = self._ecu_version == "266B"
        is_aah  = self._ecu_version == "AAH"
        rom = self._romdata

        for r in range(ROWS):
            for c in range(COLS):
                fi = self.fuel_table.item(r, c)
                if fi:
                    try:
                        raw = lambda_to_raw(float(fi.text())) if (is_266b or is_aah) else display_to_raw(float(fi.text()))
                        rom[0x0000 + r*COLS + c] = raw
                    except (ValueError, TypeError): pass
                ti = self.timing_table.item(r, c)
                if ti:
                    try: rom[0x0100 + r*COLS + c] = max(0, min(255, int(float(ti.text()))))
                    except (ValueError, TypeError): pass
                ki = self.knock_table.item(r, c)
                if ki:
                    try: rom[0x1000 + r*COLS + c] = max(0, min(255, int(float(ki.text()))))
                    except (ValueError, TypeError): pass

        try: rom[0x077E] = max(0, min(255, self.spin_inj.get_value()))
        except Exception: pass

        try: rom[0x07E1] = max(0, min(255, round(self.spin_cl_rpm.get_value() / 25)))
        except Exception: pass

        DECEL_ADDR = 0x0E30
        for i, v in enumerate(self.decel_table.get_values()):
            try: rom[DECEL_ADDR+i] = max(0, min(255, round(v / 0.3922)))
            except Exception: pass

        CL_LOAD_ADDR = 0x0660
        for i, v in enumerate(self.cl_load_table.get_values()):
            try: rom[CL_LOAD_ADDR+i] = max(0, min(255, int(v)))
            except Exception: pass

        if not is_aah:
            MAF_ADDR = 0x02D0
            for i, v in enumerate(self.maf_table.get_values()):
                try:
                    v = max(0, min(65535, int(v)))
                    rom[MAF_ADDR+i*2]   = (v >> 8) & 0xFF
                    rom[MAF_ADDR+i*2+1] = v & 0xFF
                except Exception: pass

            # Warmup Enrichment
            for i, v in enumerate(self.warmup_table.get_values()):
                try: rom[0x06D0+i] = max(0, min(255, int(v)))
                except Exception: pass

            # Overrun Curves
            for row, t in enumerate(self.overrun_tables):
                for i, v in enumerate(t.get_values()):
                    try: rom[0x0550+row*16+i] = max(0, min(255, int(v)))
                    except Exception: pass

        # Extra Decel Map (all ECUs)
        for i, v in enumerate(self.decel2a_table.get_values()):
            try: rom[0x0E40+i] = max(0, min(255, int(v)))
            except Exception: pass
        for i, v in enumerate(self.decel2b_table.get_values()):
            try: rom[0x0E48+i] = max(0, min(255, int(v)))
            except Exception: pass

        # Sensor Cal tables (266D only)
        if self._ecu_version == "266D":
            for i, v in enumerate(self.sensor_a_table.get_values()):
                try: rom[0x1120+i] = max(0, min(255, int(v)))
                except Exception: pass
            for i, v in enumerate(self.sensor_b_table.get_values()):
                try: rom[0x1130+i] = max(0, min(255, int(v)))
                except Exception: pass

        fixed = apply_checksum(bytearray(rom[:32768]), self._ecu_version)
        self._romdata[:32768] = fixed
        if len(self._romdata) == 65536:
            self._romdata[32768:] = fixed
        return bytes(self._romdata)

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open ROM", "",
            "ROM Files (*.bin *.034);;Binary Files (*.bin);;034 Files (*.034);;All Files (*)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.load_data(data, path)
            version = self._ecu_version
            if not verify_checksum(bytes(self._romdata[:32768]), version):
                from ecu_profiles import CHECKSUM_266D, CHECKSUM_266B
                cs_info = CHECKSUM_266B if version == "266B" else CHECKSUM_266D
                actual  = sum(self._romdata[:32768])
                delta   = actual - cs_info["target"]
                reply   = QMessageBox.warning(
                    self, "Invalid Checksum on Disk",
                    f"The file  '{os.path.basename(path)}'  has an invalid checksum.\n\n"
                    f"  ECU version : {version}\n"
                    f"  Byte sum    : {actual:,}  (expected {cs_info['target']:,})\n"
                    f"  Delta       : {delta:+,}\n\n"
                    "The checksum will be corrected automatically when you Save As.\n\n"
                    "If you plan to burn this file to EPROM without saving first,\n"
                    "click  'Correct Now'  to fix it in memory immediately.",
                    buttons=QMessageBox.Ok | QMessageBox.Reset,
                    defaultButton=QMessageBox.Ok)
                if reply == QMessageBox.Reset:
                    fixed = apply_checksum(bytearray(self._romdata[:32768]), version)
                    self._romdata[:32768] = fixed
                    if len(self._romdata) == 65536:
                        self._romdata[32768:] = fixed
                    self.lbl_file.setText(self.lbl_file.text().replace("⚠ checksum INVALID", "✓ checksum corrected"))
                    self.lbl_file.setStyleSheet("color: #2dff6e; font-size: 11px;")
                    self.lbl_dirty.setText("● Unsaved changes")
                    self._dirty = True
        except Exception as e:
            QMessageBox.critical(self, "Open Error", str(e))

    def _save_as_file(self):
        if self._save_path:
            start = self._save_path
        elif self._filepath:
            base  = os.path.splitext(os.path.basename(self._filepath))[0]
            start = os.path.join(os.path.dirname(self._filepath), base + "_edited.bin")
        else:
            start = "tune_edited.bin"

        path, _ = QFileDialog.getSaveFileName(
            self, "Save ROM As .bin", start, "Binary ROM Files (*.bin);;All Files (*)")
        if not path:
            return
        if path.lower().endswith(".034"):
            QMessageBox.warning(self, "Wrong Extension",
                "Cannot save as .034 — that format requires bit-scrambling.\n"
                "Save as .bin for Teensy SD card or EPROM programmer use.")
            return
        if self._filepath and os.path.abspath(path) == os.path.abspath(self._filepath):
            reply = QMessageBox.warning(
                self, "Overwrite Source File?",
                f"You are about to overwrite the original source file:\n\n"
                f"  {os.path.basename(self._filepath)}\n\n"
                "This will permanently replace the original ROM data.\n"
                "It is strongly recommended to save to a new file name.\n\n"
                "Overwrite anyway?",
                buttons=QMessageBox.Yes | QMessageBox.No,
                defaultButton=QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        self._write_file(path)

    def _write_file(self, path: str):
        try:
            pre_sum = sum(self._romdata[:32768])
            data    = self.get_data()
            with open(path, "wb") as f:
                f.write(data)
            self._save_path = path
            self._dirty     = False
            from ecu_profiles import CHECKSUM_266D, CHECKSUM_266B
            cs_info = CHECKSUM_266B if self._ecu_version == "266B" else CHECKSUM_266D
            delta   = pre_sum - cs_info["target"]
            cs_note = ("checksum already valid" if delta == 0
                       else f"checksum corrected  (delta {delta:+},  "
                            f"region 0x{cs_info['cs_from']:04X}–0x{cs_info['cs_to']:04X})")
            self.lbl_dirty.setText("")
            self.lbl_file.setText(f"  {os.path.basename(path)}  —  saved ✓   {cs_note}")
            self.lbl_file.setStyleSheet("color: #2dff6e; font-size: 11px;")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _toggle_map_addrs(self, checked: bool):
        self.wgt_map_addrs.setVisible(checked)
        self.btn_map_addrs.setText("Map Addresses ▲" if checked else "Map Addresses ▼")

    def _update_ecu_info(self, result):
        """Update the detection info strip from a DetectionResult."""
        import zlib
        conf_color = {"HIGH": "#2dff6e", "MEDIUM": "#ff9900"}.get(
            result.confidence, "#ff6666")
        self.lbl_ecu_version.setText(f"ECU: {result.version}")
        self.lbl_ecu_version.setStyleSheet(
            f"color:{conf_color}; font-size:12px; font-weight:bold; padding:2px 8px 2px 0;")
        self.lbl_ecu_confidence.setText(
            f"{result.confidence}  ({result.method})")
        self.lbl_ecu_crc.setText(
            f"CRC32: {result.crc32:#010x}" if result.crc32 else "")
        cal_parts = []
        if result.cal_name:   cal_parts.append(result.cal_name)
        if result.part_number: cal_parts.append(result.part_number)
        self.lbl_ecu_cal.setText("  ·  ".join(cal_parts))

        # Populate map address table
        maps = ECU_MAPS.get(result.version)
        if maps:
            lines = [f"  {'MAP NAME':<36} {'DATA':>6}  {'X-AX':>6}  {'Y-AX':>6}  {'SZ':>5}  TYPE"]
            lines.append("  " + "-" * 68)
            for m in maps:
                x = f"0x{m.xaxis_addr:04X}" if m.xaxis_addr else "  —  "
                y = f"0x{m.yaxis_addr:04X}" if m.yaxis_addr else "  —  "
                t = "scalar" if m.is_scalar else (f"16x16" if m.is_2d else f"1x{m.cols}")
                lines.append(
                    f"  {m.name:<36} 0x{m.data_addr:04X}  {x}  {y}  {m.size:>4}B  {t}")
            self.wgt_map_addrs.setPlainText("\n".join(lines))
        else:
            self.wgt_map_addrs.setPlainText("No map table for this ECU version.")

    def _on_edit(self, *_):
        if not self._dirty:
            self._dirty = True
            self.lbl_dirty.setText("● Unsaved changes")


# ── Main ROM Manager Tab ──────────────────────────────────────────────────────

class RomManagerTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._teensy       = None
        self._dl_worker    = None
        self._ul_worker    = None
        self._build_ui()

    def set_teensy(self, teensy):
        self._teensy = teensy
        if teensy:
            teensy.on_rom_list = self._on_rom_list
            # Inject xfer handler dispatcher into TeensySerial
            teensy._xfer_handler = None
            original_parse = teensy._parse_line

            def patched_parse(line):
                handler = getattr(teensy, "_xfer_handler", None)
                if handler and line.startswith("XFER:"):
                    self._dispatch_xfer(line, handler)
                    return
                original_parse(line)
            teensy._parse_line = patched_parse

    def _dispatch_xfer(self, line, handler):
        if line.startswith("XFER:START,"):
            parts = line[11:].split(",")
            handler.feed_xfer_start(parts[0], int(parts[1]))
        elif line.startswith("XFER:END,"):
            handler.feed_xfer_end(int(line[9:]))
        elif line.startswith("XFER:CHUNK,"):
            # chunk data comes as next raw read — handled differently
            # for download this is in the binary stream
            pass
        elif line == "ACK:ROM_UPLOAD_READY":
            if hasattr(handler, "feed_ready"):
                handler.feed_ready()
        elif line == "ACK:ROM_UPLOAD_COMPLETE":
            if hasattr(handler, "feed_complete"):
                handler.feed_complete()
        elif line.startswith("ERR:"):
            if hasattr(handler, "feed_error"):
                handler.feed_error(line[4:])

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── Top: Transfer controls ────────────────────────────────────────
        transfer_row = QHBoxLayout()
        transfer_row.setSpacing(8)

        # Download group
        grp_dl = QGroupBox("Download from Teensy SD → PC")
        dl_lay = QVBoxLayout(grp_dl)
        dl_inner = QHBoxLayout()

        self.rom_list = QListWidget()
        self.rom_list.setMaximumHeight(90)
        self.rom_list.setStyleSheet(
            "QListWidget { background:#0d1117; border:1px solid #1a2332; color:#bccdd8; }"
            "QListWidget::item:selected { background:#1a2840; color:#00d4ff; }"
        )

        dl_btns = QVBoxLayout()
        self.btn_refresh  = QPushButton("⟳ Refresh List")
        self.btn_download = QPushButton("⬇  Download Selected")
        self.btn_refresh.setEnabled(False)
        self.btn_download.setEnabled(False)
        self.btn_refresh.clicked.connect(self._refresh_roms)
        self.btn_download.clicked.connect(self._download_rom)

        dl_btns.addWidget(self.btn_refresh)
        dl_btns.addWidget(self.btn_download)
        dl_btns.addStretch()

        dl_inner.addWidget(self.rom_list, 1)
        dl_inner.addLayout(dl_btns)
        dl_lay.addLayout(dl_inner)

        # Upload group
        grp_ul = QGroupBox("Upload PC → Teensy SD")
        ul_lay = QVBoxLayout(grp_ul)
        ul_inner = QHBoxLayout()

        self.lbl_upload_file = QLabel("No file selected")
        self.lbl_upload_file.setStyleSheet("color:#3d5068; font-size:11px;")

        ul_btns = QVBoxLayout()
        self.btn_pick_upload = QPushButton("📂  Choose .bin")
        self.btn_upload      = QPushButton("⬆  Upload to Teensy")
        self.btn_upload.setEnabled(False)
        self._upload_data    = None
        self._upload_name    = None
        self.btn_pick_upload.clicked.connect(self._pick_upload_file)
        self.btn_upload.clicked.connect(self._upload_rom)

        ul_btns.addWidget(self.btn_pick_upload)
        ul_btns.addWidget(self.btn_upload)
        ul_btns.addStretch()

        ul_inner.addWidget(self.lbl_upload_file, 1)
        ul_inner.addLayout(ul_btns)
        ul_lay.addLayout(ul_inner)

        transfer_row.addWidget(grp_dl, 1)
        transfer_row.addWidget(grp_ul, 1)

        # ── Progress bar ──────────────────────────────────────────────────
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(True)
        self.progress.setStyleSheet(
            "QProgressBar { background:#0d1117; border:1px solid #1a2332; color:#bccdd8; text-align:center; height:18px; }"
            "QProgressBar::chunk { background:#00d4ff; }"
        )
        self.lbl_progress = QLabel("")
        self.lbl_progress.setStyleSheet("color:#3d5068; font-size:11px;")

        prog_row = QHBoxLayout()
        prog_row.addWidget(self.progress, 1)
        prog_row.addWidget(self.lbl_progress)

        # ── Corrections ───────────────────────────────────────────────────
        grp_corr = QGroupBox("Closed-Loop Corrections")
        corr_lay = QHBoxLayout(grp_corr)
        grp_corr.setMaximumHeight(70)

        self.btn_corr_on  = QPushButton("✔  Enable Corrections")
        self.btn_corr_off = QPushButton("✘  Disable Corrections")
        self.lbl_corr     = QLabel("Status: Unknown")
        self.lbl_corr.setStyleSheet("color:#3d5068;")
        self.btn_corr_on.setEnabled(False)
        self.btn_corr_off.setEnabled(False)
        self.btn_corr_on.clicked.connect(self._corr_on)
        self.btn_corr_off.clicked.connect(self._corr_off)

        corr_lay.addWidget(self.btn_corr_on)
        corr_lay.addWidget(self.btn_corr_off)
        corr_lay.addSpacing(16)
        corr_lay.addWidget(self.lbl_corr)
        corr_lay.addStretch()

        # ── Offline editor ────────────────────────────────────────────────
        grp_editor = QGroupBox("ROM Editor  —  Offline or Downloaded")
        editor_lay = QVBoxLayout(grp_editor)
        editor_lay.setContentsMargins(4, 12, 4, 4)

        self.lbl_editor_info = QLabel(
            "Download a ROM from Teensy, or open a .bin file, to edit maps offline. "
            "Save the .bin and upload it back when ready."
        )
        self.lbl_editor_info.setStyleSheet("color:#3d5068; font-size:11px;")
        self.lbl_editor_info.setWordWrap(True)

        self.offline_editor = OfflineRomEditor()

        # Button to push downloaded ROM straight to the editor
        btn_row = QHBoxLayout()
        self.btn_load_active = QPushButton("⬇  Load Active ROM into Editor")
        self.btn_load_active.setEnabled(False)
        self.btn_load_active.setToolTip("Download the currently active ROM and load it into the editor")
        self.btn_load_active.clicked.connect(self._load_active_rom)
        self.btn_upload_edited = QPushButton("⬆  Upload Edited ROM to Teensy")
        self.btn_upload_edited.setEnabled(False)
        self.btn_upload_edited.clicked.connect(self._upload_edited_rom)
        btn_row.addWidget(self.btn_load_active)
        btn_row.addWidget(self.btn_upload_edited)
        btn_row.addStretch()

        editor_lay.addWidget(self.lbl_editor_info)
        editor_lay.addLayout(btn_row)
        editor_lay.addWidget(self.offline_editor, 1)

        # ── Assemble ──────────────────────────────────────────────────────
        root.addLayout(transfer_row)
        root.addLayout(prog_row)
        root.addWidget(grp_corr)
        root.addWidget(grp_editor, 1)

    # ── Connection events ────────────────────────────────────────────────────

    def on_connected(self):
        for b in [self.btn_refresh, self.btn_download,
                  self.btn_corr_on, self.btn_corr_off,
                  self.btn_load_active]:
            b.setEnabled(True)
        self._check_upload_ready()
        self._refresh_roms()

    def on_disconnected(self):
        for b in [self.btn_refresh, self.btn_download,
                  self.btn_corr_on, self.btn_corr_off,
                  self.btn_load_active]:
            b.setEnabled(False)
        self.btn_upload.setEnabled(False)
        self.lbl_corr.setText("Status: Unknown")
        self.lbl_corr.setStyleSheet("color:#3d5068;")

    def update_active_rom(self, rom_file: str):
        self.btn_load_active.setText(f"⬇  Load  {rom_file}  into Editor")
        self._active_rom = rom_file

    # ── ROM list ─────────────────────────────────────────────────────────────

    def _refresh_roms(self):
        if self._teensy:
            self._teensy.list_roms()

    def _on_rom_list(self, roms):
        self.rom_list.clear()
        for r in roms:
            self.rom_list.addItem(r)

    # ── Download ──────────────────────────────────────────────────────────────

    def _download_rom(self):
        item = self.rom_list.currentItem()
        if not item or not self._teensy:
            return
        filename = item.text()
        self._start_download(filename)

    def _load_active_rom(self):
        rom = getattr(self, "_active_rom", "tune.bin")
        self._start_download(rom, load_editor=True)

    def _start_download(self, filename, load_editor=False):
        if not self._teensy:
            return
        self._dl_load_editor = load_editor
        self._dl_worker = DownloadWorker(self._teensy, filename)
        self._dl_worker.progress.connect(self._on_dl_progress)
        self._dl_worker.complete.connect(self._on_dl_complete)
        self._dl_worker.error.connect(self._on_xfer_error)
        self._show_progress(f"Downloading {filename}...")
        self._dl_worker.start()

    def _on_dl_progress(self, done, total):
        if total > 0:
            self.progress.setMaximum(total)
            self.progress.setValue(done)
            kb_done = done // 1024
            kb_total = total // 1024
            self.lbl_progress.setText(f"{kb_done} / {kb_total} KB")

    def _on_dl_complete(self, data, filename):
        self._hide_progress()
        self.lbl_progress.setText(f"✓ Downloaded {filename}  ({len(data):,} bytes)")
        self.lbl_progress.setStyleSheet("color:#2dff6e; font-size:11px;")

        if getattr(self, "_dl_load_editor", False):
            self.offline_editor.load_data(data)
            self.btn_upload_edited.setEnabled(True)
        else:
            # Ask where to save
            path, _ = QFileDialog.getSaveFileName(
                self, f"Save {filename}", filename,
                "Binary Files (*.bin);;All Files (*)"
            )
            if path:
                try:
                    with open(path, "wb") as f:
                        f.write(data)
                    self.lbl_progress.setText(f"✓ Saved to {os.path.basename(path)}")
                    # Also offer to load into editor
                    self.offline_editor.load_data(data, path)
                    self.btn_upload_edited.setEnabled(True)
                except Exception as e:
                    QMessageBox.critical(self, "Save Error", str(e))

    # ── Upload ────────────────────────────────────────────────────────────────

    def _pick_upload_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose ROM .bin to Upload", "",
            "Binary Files (*.bin);;All Files (*)"
        )
        if path:
            try:
                with open(path, "rb") as f:
                    self._upload_data = f.read()
                self._upload_name = os.path.basename(path)
                self.lbl_upload_file.setText(
                    f"{self._upload_name}  ({len(self._upload_data):,} bytes)"
                )
                self.lbl_upload_file.setStyleSheet("color:#bccdd8; font-size:11px;")
                self._check_upload_ready()
            except Exception as e:
                QMessageBox.critical(self, "Open Error", str(e))

    def _check_upload_ready(self):
        self.btn_upload.setEnabled(
            bool(self._upload_data and self._teensy and self._teensy.is_connected())
        )

    def _upload_rom(self):
        if not self._upload_data or not self._teensy:
            return
        self._ul_worker = UploadWorker(
            self._teensy, self._upload_name, self._upload_data
        )
        self._ul_worker.progress.connect(self._on_ul_progress)
        self._ul_worker.complete.connect(self._on_ul_complete)
        self._ul_worker.error.connect(self._on_xfer_error)
        self._show_progress(f"Uploading {self._upload_name}...")
        self._ul_worker.start()

    def _upload_edited_rom(self):
        """Upload the currently edited offline ROM back to Teensy."""
        if not self._teensy or not self._teensy.is_connected():
            QMessageBox.warning(self, "Not Connected", "Connect to Teensy first.")
            return
        data = self.offline_editor.get_data()
        # Use same filename as was loaded, or ask
        name = None
        if self.offline_editor._filepath:
            name = os.path.basename(self.offline_editor._filepath)
        if not name:
            name = "tune.bin"

        reply = QMessageBox.question(
            self, "Upload to Teensy",
            f"Upload edited ROM as  '{name}'  to Teensy SD card?\n\n"
            f"Size: {len(data):,} bytes",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self._ul_worker = UploadWorker(self._teensy, name, data)
        self._ul_worker.progress.connect(self._on_ul_progress)
        self._ul_worker.complete.connect(self._on_ul_complete)
        self._ul_worker.error.connect(self._on_xfer_error)
        self._show_progress(f"Uploading edited ROM as {name}...")
        self._ul_worker.start()

    def _on_ul_progress(self, done, total):
        if total > 0:
            self.progress.setMaximum(total)
            self.progress.setValue(done)
            kb_done  = done  // 1024
            kb_total = total // 1024
            self.lbl_progress.setText(f"{kb_done} / {kb_total} KB")

    def _on_ul_complete(self, filename):
        self._hide_progress()
        self.lbl_progress.setText(f"✓ Uploaded  {filename}  successfully")
        self.lbl_progress.setStyleSheet("color:#2dff6e; font-size:11px;")
        self._refresh_roms()

    # ── Error ─────────────────────────────────────────────────────────────────

    def _on_xfer_error(self, msg):
        self._hide_progress()
        self.lbl_progress.setText(f"✗ Error: {msg}")
        self.lbl_progress.setStyleSheet("color:#ff3333; font-size:11px;")
        QMessageBox.critical(self, "Transfer Error", msg)

    # ── Progress helpers ──────────────────────────────────────────────────────

    def _show_progress(self, msg):
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.lbl_progress.setText(msg)
        self.lbl_progress.setStyleSheet("color:#ff9900; font-size:11px;")

    def _hide_progress(self):
        self.progress.setVisible(False)

    # ── Corrections ───────────────────────────────────────────────────────────

    def _corr_on(self):
        if self._teensy:
            self._teensy.corrections_on()
            self.lbl_corr.setText("Status: ENABLED")
            self.lbl_corr.setStyleSheet("color:#2dff6e;")

    def _corr_off(self):
        if self._teensy:
            self._teensy.corrections_off()
            self.lbl_corr.setText("Status: DISABLED")
            self.lbl_corr.setStyleSheet("color:#ff9900;")
