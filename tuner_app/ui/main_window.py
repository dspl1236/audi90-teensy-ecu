"""
ui/main_window.py
Main application window — assembles connection panel + all tabs.
"""

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QTabWidget,
    QStatusBar, QLabel, QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer, pyqtSlot
from PyQt5.QtGui import QFont

from ui.connection_panel import ConnectionPanel
from ui.gauges_tab       import GaugesTab
from ui.map_editor_tab   import MapEditorTab
from ui.rom_manager_tab  import RomManagerTab
from ui.datalog_tab      import DatalogTab
from ui.console_tab      import ConsoleTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Audi 90 2.6L Stroker — Teensy 4.1 Tuner  v1.1.0")
        self.setMinimumSize(1280, 800)
        self.resize(1440, 900)

        self._teensy = None
        self._build_ui()
        self._setup_status_bar()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Connection panel (top bar) ────────────────────────────────────
        self.conn_panel = ConnectionPanel()
        self.conn_panel.sig_connected.connect(self._on_connected)
        self.conn_panel.sig_disconnected.connect(self._on_disconnected)
        self.conn_panel.sig_log.connect(self._on_log)
        self.conn_panel.setMaximumHeight(110)

        # ── Tab area ──────────────────────────────────────────────────────
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        self.tab_gauges  = GaugesTab()
        self.tab_maps    = MapEditorTab()
        self.tab_rom     = RomManagerTab()
        self.tab_datalog = DatalogTab()
        self.tab_console = ConsoleTab()

        self.tabs.addTab(self.tab_gauges,  "Live Gauges")
        self.tabs.addTab(self.tab_maps,    "Map Editor")
        self.tabs.addTab(self.tab_rom,     "ROM Manager")
        self.tabs.addTab(self.tab_datalog, "Datalog")
        self.tabs.addTab(self.tab_console, "Console")

        root.addWidget(self.conn_panel)
        root.addWidget(self.tabs, 1)

        # ── Live gauge → map highlight bridge ────────────────────────────
        # Forward live data to map editor for operating-cell highlight
        self.tab_gauges.g_rpm   # touch to ensure init
        self._orig_gauge_data_cb = None

    # ── Connection events ────────────────────────────────────────────────────

    @pyqtSlot(object)
    def _on_connected(self, teensy):
        self._teensy = teensy

        # Wire log to console
        original_on_log = teensy.on_log
        def combined_log(msg):
            if original_on_log:
                original_on_log(msg)
            self.tab_console.append_log(msg)
        teensy.on_log = combined_log

        # Wire live data to map highlight
        self.tab_gauges.set_teensy(teensy)
        orig_live = self.tab_gauges._on_data
        def live_and_highlight(data):
            orig_live(data)
            self.tab_maps.highlight_operating_cell(data.rpm, data.map_kpa)
        teensy.on_live_data = live_and_highlight

        # Wire status to ROM tab
        orig_status = teensy.on_status
        def status_and_rom(status):
            if orig_status:
                orig_status(status)
            if status.rom_file:
                self.tab_rom.update_active_rom(status.rom_file)
        teensy.on_status = status_and_rom

        self.tab_maps.set_teensy(teensy)
        self.tab_rom.set_teensy(teensy)
        self.tab_console.set_teensy(teensy)

        self.tab_maps.on_connected()
        self.tab_rom.on_connected()

        self.statusbar.showMessage("Connected", 3000)
        self._set_status_light(True)
        self.tab_console.append_log("[SYSTEM] Connected")

    @pyqtSlot()
    def _on_disconnected(self):
        self._teensy = None
        self.tab_gauges.set_teensy(None)
        self.tab_maps.set_teensy(None)
        self.tab_rom.set_teensy(None)
        self.tab_console.set_teensy(None)

        self.tab_maps.on_disconnected()
        self.tab_rom.on_disconnected()

        self.statusbar.showMessage("Disconnected", 3000)
        self._set_status_light(False)
        self.tab_console.append_log("[SYSTEM] Disconnected")

    @pyqtSlot(str)
    def _on_log(self, msg: str):
        self.tab_console.append_log(msg)

    # ── Status bar ────────────────────────────────────────────────────────────

    def _setup_status_bar(self):
        self.statusbar = self.statusBar()
        self.statusbar.setStyleSheet(
            "QStatusBar { background: #0d1117; color: #3d5068; "
            "border-top: 1px solid #1a2332; font-size: 11px; }"
        )

        self.lbl_sb_left  = QLabel("Audi 90 2.6L Stroker  |  Teensy 4.1  |  893906266D")
        self.lbl_sb_right = QLabel("v1.1.0")
        self.lbl_sb_left.setStyleSheet("color: #3d5068; padding: 0 8px;")
        self.lbl_sb_right.setStyleSheet("color: #3d5068; padding: 0 8px;")

        self.statusbar.addWidget(self.lbl_sb_left)
        self.statusbar.addPermanentWidget(self.lbl_sb_right)

        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status_bar)
        self._status_timer.start(1000)

    def _update_status_bar(self):
        if self._teensy and self._teensy.is_connected():
            last = self.tab_gauges._last_data
            self.lbl_sb_left.setText(
                f"Audi 90 2.6L  |  "
                f"RPM: {last.rpm}  |  "
                f"AFR: {last.afr:.2f}  |  "
                f"MAP: {last.map_kpa:.0f} kPa  |  "
                f"Trim: {last.fuel_trim_pct:+.1f}%"
            )
        else:
            self.lbl_sb_left.setText("Audi 90 2.6L Stroker  |  Teensy 4.1  |  893906266D")

    def _set_status_light(self, connected: bool):
        color = "#2dff6e" if connected else "#3d5068"
        self.lbl_sb_right.setStyleSheet(f"color: {color}; padding: 0 8px;")
        self.lbl_sb_right.setText("● LIVE" if connected else "● OFFLINE")
