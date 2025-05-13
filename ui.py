# implements the main user interface using PySide6 widgets
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QComboBox, QProgressBar, QTextEdit,
    QFileDialog, QMessageBox, QCheckBox
)
from PySide6.QtCore import Qt, QThread, Signal, Slot, QTimer
from PySide6.QtGui import QFontMetrics, QTextCursor, QDragEnterEvent, QDropEvent

from utils import (
    DEFAULT_FPS, DEFAULT_WIDTH, DEFAULT_HEIGHT,
    DITHER_OPTIONS_FULL, DEFAULT_DITHER_SHORT_KEY,
    get_video_duration, show_error_message,
    validate_output_path, MAX_FPS, MAX_WIDTH, MAX_HEIGHT,
    DEFAULT_SPEED, MAX_SPEED, WEBP_QUALITY, WEBP_COMPRESSION,
    PALETTE_MODE_OPTIONS, DEFAULT_PALETTE_MODE_KEY
)
from models import ConverterSettings
from worker import Worker

class DitherComboBox(QComboBox):
    def __init__(self, options_map, default_short_key, parent=None):
        super().__init__(parent)
        self.options_map = options_map

        for _, (long_display, internal_value) in self.options_map.items():
            self.addItem(long_display, internal_value)

        if default_short_key in self.options_map:
            default_internal_val = self.options_map[default_short_key][1]
            default_index = self.findData(default_internal_val)
            if default_index != -1:
                self.setCurrentIndex(default_index)

class WheelSpinBox(QLineEdit):
    def __init__(self, step=1, minimum=1, maximum=9999, parent=None):
        super().__init__(parent)
        self.step = step
        self.minimum = minimum
        self.maximum = maximum
        self.setText(str(self.minimum))

    def wheelEvent(self, event):
        try:
            val = int(self.text())
        except ValueError:
            val = self.minimum

        delta = event.angleDelta().y() // 120

        if event.modifiers() & Qt.ControlModifier:
            step = 1
        elif event.modifiers() & Qt.ShiftModifier:
            step = self.step * 5
        else:
            step = self.step

        if val == -1 and delta > 0:
            val = 0
        else:
            val += delta * step

        if self.minimum == -1:
            val = max(-1, min(self.maximum, val))
        else:
            val = max(self.minimum, min(self.maximum, val))

        self.setText(str(val))
        event.accept()

class GIFConverterApp(QWidget):
    cancellation_request_signal = Signal()

    def __init__(self, dependency_paths):
        super().__init__()
        ffmpeg_path_str, ffmpeg_source = dependency_paths["ffmpeg"]
        gifsicle_path_str, gifsicle_source = dependency_paths["gifsicle"]
        ffprobe_path_str, ffprobe_source = dependency_paths["ffprobe"]

        self.ffmpeg_path = Path(ffmpeg_path_str)
        self.gifsicle_path = Path(gifsicle_path_str)
        self.ffprobe_path = Path(ffprobe_path_str)

        self.worker_thread = None
        self.worker = None

        self._init_ui()
        self.setAcceptDrops(True)

        self._append_plain_log(f"Using ffmpeg: {self.ffmpeg_path} (from {ffmpeg_source})", False)
        self._append_plain_log(f"Using gifsicle: {self.gifsicle_path} (from {gifsicle_source})", False)
        self._append_plain_log(f"Using ffprobe: {self.ffprobe_path} (from {ffprobe_source})", False)
        self._append_plain_log("Application started. Please select an input file.", False)

    def _init_ui(self):
        self.setWindowTitle("Video to GIF Converter")
        self.resize(600, 600)

        main_layout = QVBoxLayout(self)

        file_selection_group = QWidget()
        file_selection_layout = QGridLayout(file_selection_group)
        file_selection_layout.setContentsMargins(0, 0, 0, 0)

        file_selection_layout.addWidget(QLabel("Input Video File:"), 0, 0)
        self.input_file_var = QLineEdit()
        self.input_file_var.setPlaceholderText("Select or drop video file…")
        file_selection_layout.addWidget(self.input_file_var, 0, 1)
        browse_input_btn = QPushButton("Browse…")
        browse_input_btn.clicked.connect(self.browse_input_file)
        file_selection_layout.addWidget(browse_input_btn, 0, 2)

        file_selection_layout.addWidget(QLabel("Output GIF File:"), 1, 0)
        self.output_file_var = QLineEdit()
        self.output_file_var.setPlaceholderText("Specify output GIF path…")
        file_selection_layout.addWidget(self.output_file_var, 1, 1)
        browse_output_btn = QPushButton("Save As…")
        browse_output_btn.clicked.connect(self.browse_output_file)
        file_selection_layout.addWidget(browse_output_btn, 1, 2)

        file_selection_layout.setColumnStretch(1, 1)
        main_layout.addWidget(file_selection_group)

        params_frame = QGroupBox("Conversion Parameters")
        params_layout = QGridLayout(params_frame)
        small_entry_width = QFontMetrics(self.font()).averageCharWidth() * 7

        max_fps = MAX_FPS
        max_width = MAX_WIDTH
        max_height = MAX_HEIGHT

        params_layout.addWidget(QLabel("FPS:"), 0, 0)
        self.fps_var = WheelSpinBox(step=1, minimum=-1, maximum=max_fps)
        self.fps_var.setText(str(DEFAULT_FPS))
        self.fps_var.setToolTip("Mouse wheel to adjust FPS. Ctrl=Fine, Shift=Fast.")
        self.fps_var.setMaximumWidth(small_entry_width)
        params_layout.addWidget(self.fps_var, 0, 1, Qt.AlignmentFlag.AlignLeft)
        params_layout.addWidget(QLabel("(-1 for source)"), 0, 2)

        params_layout.addWidget(QLabel("Width:"), 1, 0)
        self.width_var = WheelSpinBox(step=10, minimum=-1, maximum=max_width)
        self.width_var.setText(str(DEFAULT_WIDTH))
        self.width_var.setToolTip("Mouse wheel to adjust width. Ctrl=Fine, Shift=Fast.")
        self.width_var.setMaximumWidth(small_entry_width)
        params_layout.addWidget(self.width_var, 1, 1, Qt.AlignmentFlag.AlignLeft)
        params_layout.addWidget(QLabel("(px, -1 for auto)"), 1, 2)

        params_layout.addWidget(QLabel("Height:"), 2, 0)
        self.height_var = WheelSpinBox(step=10, minimum=-1, maximum=max_height)
        self.height_var.setText(str(DEFAULT_HEIGHT))
        self.height_var.setToolTip("Mouse wheel to adjust height. Ctrl=Fine, Shift=Fast.")
        self.height_var.setMaximumWidth(small_entry_width)
        params_layout.addWidget(self.height_var, 2, 1, Qt.AlignmentFlag.AlignLeft)
        params_layout.addWidget(QLabel("(px, -1 for auto)"), 2, 2)

        params_layout.addWidget(QLabel("Speed Multiplier:"), 3, 0)
        from PySide6.QtWidgets import QDoubleSpinBox

        from PySide6.QtWidgets import QAbstractSpinBox
        class SpeedSpinBox(QDoubleSpinBox):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.setButtonSymbols(QAbstractSpinBox.NoButtons)
            def wheelEvent(self, event):
                try:
                    val = float(self.text())
                except ValueError:
                    val = 1.0
                delta = event.angleDelta().y() // 120
                if event.modifiers() & Qt.ControlModifier:
                    step = 0.01
                elif event.modifiers() & Qt.ShiftModifier:
                    step = 1.0
                else:
                    step = 0.1
                val += delta * step
                val = max(self.minimum(), min(self.maximum(), val))
                self.setValue(val)
                event.accept()

        self.speed_multiplier_var = SpeedSpinBox()
        self.speed_multiplier_var.setRange(0.1, MAX_SPEED)
        self.speed_multiplier_var.setSingleStep(0.1)
        self.speed_multiplier_var.setValue(DEFAULT_SPEED)
        self.speed_multiplier_var.setToolTip("Set to >1.0 to speed up, <1.0 to slow down the GIF.")
        self.speed_multiplier_var.setFixedWidth(small_entry_width)
        params_layout.addWidget(self.speed_multiplier_var, 3, 1, Qt.AlignmentFlag.AlignLeft)
        params_layout.addWidget(QLabel("(e.g., 2.0 = 2x faster)"), 3, 2)

        self.palette_mode_var = QComboBox()
        for _, (desc, val) in PALETTE_MODE_OPTIONS.items():
            self.palette_mode_var.addItem(desc, val)

        default_index = self.palette_mode_var.findData(
            PALETTE_MODE_OPTIONS[DEFAULT_PALETTE_MODE_KEY][1])
        if default_index != -1:
            self.palette_mode_var.setCurrentIndex(default_index)

        params_layout.addWidget(QLabel("Palette:"), 4, 0)
        params_layout.addWidget(self.palette_mode_var, 4, 1, 1, 2)

        params_layout.addWidget(QLabel("Dithering:"), 5, 0)
        self.quality_var = DitherComboBox(DITHER_OPTIONS_FULL, DEFAULT_DITHER_SHORT_KEY)
        params_layout.addWidget(self.quality_var, 5, 1, 1, 2)

        self.loop_checkbox = QCheckBox("Loop animation")
        self.loop_checkbox.setChecked(True)
        self.loop_checkbox.setToolTip("Enable infinite loop (both GIF and WebP).")
        params_layout.addWidget(self.loop_checkbox, 6, 0, 1, 3)

        params_layout.setColumnStretch(2, 1)
        main_layout.addWidget(params_frame)

        webp_params_frame = QGroupBox("WebP Parameters")
        webp_params_layout = QGridLayout(webp_params_frame)

        webp_params_layout.addWidget(QLabel("Quality:"), 0, 0)
        self.webp_quality_var = WheelSpinBox(step=1, minimum=0, maximum=100)
        self.webp_quality_var.setText(str(WEBP_QUALITY))
        self.webp_quality_var.setToolTip("WebP quality (0–100). Higher = better.")
        self.webp_quality_var.setMaximumWidth(small_entry_width)
        webp_params_layout.addWidget(self.webp_quality_var, 0, 1)

        webp_params_layout.addWidget(QLabel("Compression Level:"), 1, 0)
        self.webp_compression_var = WheelSpinBox(step=1, minimum=0, maximum=6)
        self.webp_compression_var.setText(str(WEBP_COMPRESSION))
        self.webp_compression_var.setToolTip("0 = fastest, 6 = smallest. Only applies to lossy WebP.")
        self.webp_compression_var.setMaximumWidth(small_entry_width)
        webp_params_layout.addWidget(self.webp_compression_var, 1, 1)

        self.webp_lossless_checkbox = QCheckBox("Lossless")
        self.webp_lossless_checkbox.setToolTip("Enable true lossless WebP (ignores quality/compression settings)")
        webp_params_layout.addWidget(self.webp_lossless_checkbox, 2, 0, 1, 2)

        self.webp_lossless_checkbox.toggled.connect(self._update_webp_options_state)

        main_layout.addWidget(webp_params_frame)

        action_button_layout = QHBoxLayout()
        self.generate_button = QPushButton("Generate GIF")
        self.generate_button.clicked.connect(self.start_conversion)
        action_button_layout.addWidget(self.generate_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.request_worker_cancellation)
        self.cancel_button.setVisible(False)
        action_button_layout.addWidget(self.cancel_button)
        main_layout.addLayout(action_button_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        main_layout.addWidget(self.progress_bar)

        self.progress_label = QLabel("Progress: 0%")

        main_layout.addWidget(self.progress_label, alignment=Qt.AlignmentFlag.AlignCenter)

        log_frame = QGroupBox("Log")
        log_layout = QVBoxLayout(log_frame)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(150)
        log_layout.addWidget(self.log_text)
        main_layout.addWidget(log_frame)
        main_layout.setStretchFactor(log_frame, 1)

        self.output_file_var.textChanged.connect(self._update_webp_options_state)

    @Slot(str, bool)
    def _append_plain_log(self, message, clear_first):
        if clear_first:
            self.log_text.clear()
        self.log_text.append(message)
        self.log_text.ensureCursorVisible()

    @Slot(str, bool)
    def _append_html_log(self, html_message, clear_first):
        if clear_first:
            self.log_text.clear()
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(html_message + "<br>")
        self.log_text.ensureCursorVisible()

    @Slot(int, str)
    def _update_progress_bar(self, value: int, text: str):
        if value < 0:
            self.progress_bar.setRange(0, 0)
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(value)
        self.progress_label.setText(text or f"Progress: {value}%")

    @Slot(bool, str, str, bool)
    def _handle_generation_result(self, success, title, message, is_error):
        self.generate_button.setVisible(True)
        self.generate_button.setEnabled(True)
        self.cancel_button.setVisible(False)

        if self.worker_thread:
            self.worker_thread.quit()
            self.worker_thread.wait()
            self.worker_thread = None
            self.worker = None

        if is_error:
            show_error_message(title, message, self)
        elif title == "Cancelled":
            QMessageBox.information(self, title, message)

    def _cleanup_worker_thread(self):
        if self.worker:
            try:
                if hasattr(self.worker, "log_plain_signal"):
                    self.worker.log_plain_signal.disconnect()
                if hasattr(self.worker, "log_html_signal"):
                    self.worker.log_html_signal.disconnect()
                if hasattr(self.worker, "progress_signal"):
                    self.worker.progress_signal.disconnect()
                if hasattr(self.worker, "finished_signal"):
                    self.worker.finished_signal.disconnect()
            except (TypeError, RuntimeError):
                pass
            try:
                self.worker.deleteLater()
            except RuntimeError:
                pass
            self.worker = None

        if self.worker_thread:
            try:
                self.worker_thread.quit()
                self.worker_thread.wait()
                self.worker_thread.deleteLater()
            except RuntimeError:
                pass
            self.worker_thread = None

    def browse_input_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Video File", "",
            "Video files (*.mp4 *.mov *.mkv *.avi *.webm);;All files (*.*)"
        )
        if file_path:
            self._handle_new_input_file(file_path)

    def _handle_new_input_file(self, file_path):
        self.input_file_var.setText(file_path)
        self._append_plain_log(f"Input file set: {file_path}", False)
        base = Path(file_path).stem
        suggested_output = Path(file_path).with_name(f"{base}.gif")
        self.output_file_var.setText(str(suggested_output))
        self._append_plain_log(f"Suggested output: {suggested_output}", False)

    def browse_output_file(self):
        current_output = Path(self.output_file_var.text())
        current_input = Path(self.input_file_var.text())
        initial_dir = current_output.parent if current_output.parent.exists() else \
                      (current_input.parent if current_input.parent.exists() else Path.cwd())
        initial_file = current_output.name if current_output.name else f"{current_input.stem}.gif"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save GIF As", str(initial_dir / initial_file),
            "GIF files (*.gif);;All files (*.*)"
        )
        if file_path:
            self.output_file_var.setText(file_path)
            self._append_plain_log(f"Output file set to: {file_path}", False)

    def _update_webp_options_state(self):
        output_path = self.output_file_var.text().lower()
        is_webp = output_path.endswith(".webp")

        self.quality_var.setEnabled(not is_webp)  # Disable dithering for webp
        self.webp_quality_var.setEnabled(is_webp and not self.webp_lossless_checkbox.isChecked())
        self.webp_compression_var.setEnabled(is_webp and not self.webp_lossless_checkbox.isChecked())
        self.webp_lossless_checkbox.setEnabled(is_webp)
        self.palette_mode_var.setEnabled(not is_webp)

        # Update generate button text
        self.generate_button.setText("Generate WebP" if is_webp else "Generate GIF")

        # Update window title
        self.setWindowTitle("Video to WebP Converter" if is_webp else "Video to GIF Converter")

    def start_conversion(self):
        input_path = Path(self.input_file_var.text())
        output_path = Path(self.output_file_var.text())
        duration = get_video_duration(self.ffprobe_path, input_path, log_callback=self._append_plain_log)
        webp_lossless = self.webp_lossless_checkbox.isChecked()

        if duration is None:
            self._append_plain_log("⚠️ Warning: Could not determine video duration — progress will be estimated.", False)

        if not input_path.is_file():
            show_error_message("Error", f"Input file not found:\n{input_path}", self)
            return

        is_valid, msg = validate_output_path(output_path)
        if not is_valid:
            show_error_message("Error", msg, self)
            return

        try:
            fps = int(self.fps_var.text())
            w = int(self.width_var.text())
            h = int(self.height_var.text())
            if fps < -1 or fps == 0:
                raise ValueError("FPS must be -1 or a positive integer.")
            if w == 0 or h == 0:
                raise ValueError("Width and height must not be 0.")
            if w < -1 or h < -1:
                raise ValueError("Width and height must be >0 or -1.")
        except ValueError as e:
            show_error_message("Error", str(e), self)
            return

        self.generate_button.setVisible(False)
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(True)
        self._update_progress_bar(0, "Starting…")

        speed_multiplier = self.speed_multiplier_var.value()

        webp_quality = int(self.webp_quality_var.text())
        webp_compression = int(self.webp_compression_var.text())

        palette_mode = self.palette_mode_var.currentData()

        settings = ConverterSettings(
            input_file=input_path,
            output_file=output_path,
            fps=fps, width=w, height=h,
            dither_setting=self.quality_var.currentData(),
            ffmpeg_path=self.ffmpeg_path,
            ffprobe_path=self.ffprobe_path,
            gifsicle_path=self.gifsicle_path,
            total_duration=duration,
            speed_multiplier=speed_multiplier,
            webp_lossless=webp_lossless,
            webp_compression=webp_compression,
            webp_quality=webp_quality,
            loop=self.loop_checkbox.isChecked(),
            palette_mode=palette_mode
        )

        self.worker_thread = QThread()
        self.worker = Worker(settings)
        self.worker.moveToThread(self.worker_thread)

        self.worker.log_plain_signal.connect(self._append_plain_log)
        self.worker.log_html_signal.connect(self._append_html_log)
        self.worker.progress_signal.connect(self._update_progress_bar)
        self.worker.finished_signal.connect(self._handle_generation_result)
        self.cancellation_request_signal.connect(self.worker.request_cancellation_slot)

        self.worker_thread.started.connect(self.worker.run_conversion)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)

        self.worker_thread.start()

    @Slot()
    def request_worker_cancellation(self):
        if self.worker and self.worker_thread and self.worker_thread.isRunning():
            self._append_plain_log("UI: Sending cancellation request to worker…", False)
            self.cancellation_request_signal.emit()
            self.cancel_button.setEnabled(False)
            self.progress_label.setText("Cancelling…")

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and url.toLocalFile().lower().endswith(('.mp4', '.mov', '.mkv', '.avi', '.webm')):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    self._handle_new_input_file(url.toLocalFile())
                    event.acceptProposedAction()
                    return
        event.ignore()

    def closeEvent(self, event):
        if self.worker_thread and self.worker_thread.isRunning():
            self._append_plain_log("App closing — requesting cancellation…", False)
            self.cancellation_request_signal.emit()
            self.worker_thread.quit()
            if not self.worker_thread.wait(3000):
                self._append_plain_log("Worker did not quit cleanly. Forcing exit.", False)
        self._cleanup_worker_thread()
        super().closeEvent(event)
