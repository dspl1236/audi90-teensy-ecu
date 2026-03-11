"""
ui/rom_manager_tab.py
ROM Manager — Download from Teensy SD, Upload to Teensy SD,
Offline map editing, corrections toggle.
v1.3.0
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

FUEL_MAP_ADDR   = 0x0000   # 266D Primary Fueling  — 18×16 = 288 bytes
TIMING_MAP_ADDR = 0x0120   # 266D Primary Timing   — starts after fuel map (0x0000 + 288)
ROM_SIZE        = 65536
MAP_SIZE        = ROWS * COLS   # 288 bytes per map (18 RPM rows × 16 Load cols)


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

class OfflineRomEditor(QWidget):
    """
    Load a .bin file from disk, view/edit fuel and timing maps,
    save changes back to disk. No Teensy connection required.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self._filepath = None
        self._romdata  = bytearray(ROM_SIZE)
        self._dirty    = False
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        # ── Toolbar ───────────────────────────────────────────────────────
        toolbar = QHBoxLayout()

        self.btn_open   = QPushButton("📂  Open .bin")
        self.btn_save   = QPushButton("💾  Save .bin")
        self.btn_saveas = QPushButton("💾  Save As...")
        self.lbl_file   = QLabel("No file loaded  —  open a .bin or download from Teensy")
        self.lbl_file.setStyleSheet("color: #3d5068; font-size: 11px;")
        self.lbl_dirty  = QLabel("")
        self.lbl_dirty.setStyleSheet("color: #ff9900; font-size: 11px;")

        self.btn_save.setEnabled(False)
        self.btn_saveas.setEnabled(False)

        self.btn_open.clicked.connect(self._open_file)
        self.btn_save.clicked.connect(self._save_file)
        self.btn_saveas.clicked.connect(self._save_as_file)

        toolbar.addWidget(self.btn_open)
        toolbar.addWidget(self.btn_save)
        toolbar.addWidget(self.btn_saveas)
        toolbar.addSpacing(12)
        toolbar.addWidget(self.lbl_file)
        toolbar.addWidget(self.lbl_dirty)
        toolbar.addStretch()

        # ── Map tabs ──────────────────────────────────────────────────────
        self.map_tabs = QTabWidget()

        fuel_widget = QWidget()
        fl = QVBoxLayout(fuel_widget)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(QLabel("Fuel Map — 18×16  |  Rows = RPM  |  Cols = Load (MAP kPa)",
                    styleSheet="color:#3d5068; font-size:11px; padding:4px 0;"))
        self.fuel_table = MapTable("fuel")
        self.fuel_table._teensy = None
        self.fuel_table.itemChanged.connect(self._on_edit)
        fl.addWidget(self.fuel_table)
        self.map_tabs.addTab(fuel_widget, "Fuel Map")

        timing_widget = QWidget()
        tl = QVBoxLayout(timing_widget)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(QLabel("Timing Map — 18×16  |  Rows = RPM  |  Cols = Load (MAP kPa)",
                    styleSheet="color:#3d5068; font-size:11px; padding:4px 0;"))
        self.timing_table = MapTable("timing")
        self.timing_table._teensy = None
        self.timing_table.itemChanged.connect(self._on_edit)
        tl.addWidget(self.timing_table)
        self.map_tabs.addTab(timing_widget, "Timing Map")

        root.addLayout(toolbar)
        root.addWidget(self.map_tabs, 1)

    # ── File I/O ──────────────────────────────────────────────────────────────

    def load_data(self, data: bytes, filepath: str = None):
        """Load ROM bytes — called from download or open file."""
        if len(data) < ROM_SIZE:
            data = data + bytes(ROM_SIZE - len(data))
        self._romdata = bytearray(data[:ROM_SIZE])
        self._filepath = filepath
        self._dirty   = False

        fuel_data   = list(self._romdata[FUEL_MAP_ADDR:FUEL_MAP_ADDR + MAP_SIZE])
        timing_data = list(self._romdata[TIMING_MAP_ADDR:TIMING_MAP_ADDR + MAP_SIZE])

        self.fuel_table.load_data(fuel_data)
        self.timing_table.load_data(timing_data)

        name = os.path.basename(filepath) if filepath else "downloaded ROM"
        self.lbl_file.setText(f"  {name}  —  {len(data):,} bytes")
        self.lbl_file.setStyleSheet("color: #2dff6e; font-size: 11px;")
        self.lbl_dirty.setText("")
        self.btn_save.setEnabled(True)
        self.btn_saveas.setEnabled(True)

    def get_data(self) -> bytes:
        """Get current ROM bytes with map edits applied."""
        # Flush table edits back into romdata
        for r in range(ROWS):
            for c in range(COLS):
                fi = self.fuel_table.item(r, c)
                ti = self.timing_table.item(r, c)
                if fi:
                    try:
                        self._romdata[FUEL_MAP_ADDR + r * COLS + c] = int(fi.text())
                    except ValueError:
                        pass
                if ti:
                    try:
                        self._romdata[TIMING_MAP_ADDR + r * COLS + c] = int(ti.text())
                    except ValueError:
                        pass
        return bytes(self._romdata)

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open ROM .bin", "", "Binary Files (*.bin);;All Files (*)"
        )
        if path:
            try:
                with open(path, "rb") as f:
                    data = f.read()
                self.load_data(data, path)
            except Exception as e:
                QMessageBox.critical(self, "Open Error", str(e))

    def _save_file(self):
        if not self._filepath:
            self._save_as_file()
            return
        self._write_file(self._filepath)

    def _save_as_file(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save ROM .bin", "", "Binary Files (*.bin);;All Files (*)"
        )
        if path:
            self._filepath = path
            self._write_file(path)

    def _write_file(self, path: str):
        try:
            data = self.get_data()
            with open(path, "wb") as f:
                f.write(data)
            self._dirty = False
            self.lbl_dirty.setText("")
            name = os.path.basename(path)
            self.lbl_file.setText(f"  {name}  —  saved ✓")
            self.lbl_file.setStyleSheet("color: #2dff6e; font-size: 11px;")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    def _on_edit(self):
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
