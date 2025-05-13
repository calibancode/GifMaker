# runs gif conversion  in a background thread, reporting back progress and logs
import re
import tempfile
from enum import Enum, auto
from pathlib import Path
from utils import get_video_fps

from PySide6.QtCore import (
    QObject, Signal, Slot, QProcess,
    QTimer, QProcessEnvironment
)

from models import ConverterSettings

# these used to be hardcoded magic strings. IDEs don't like that
class LogPrefix(str, Enum):
    FFMPEG_RENDER = "ffmpeg-render"
    FFMPEG_PALETTE = "ffmpeg-palette"
    GIFSICLE_OPTIMIZE = "gifsicle-optimize"

class Step(Enum):
    IDLE      = auto()
    PALETTE   = auto()
    RENDER    = auto()
    OPTIMIZE  = auto()
    FINISHED  = auto()

# arbitrary weights for fake progress smoothness. a pleasant lie.
# these should sum to 100 for logical percentage mapping
STEP_WEIGHTS = {
    Step.PALETTE: 10,
    Step.RENDER: 70,
    Step.OPTIMIZE: 20
}

class Worker(QObject):

    log_plain_signal   = Signal(str, bool)
    log_html_signal    = Signal(str, bool)
    progress_signal    = Signal(int, str)
    finished_signal    = Signal(bool, str, str, bool)

    def __init__(self, settings: ConverterSettings):
        super().__init__()
        self.settings         = settings
        self._is_cancelled    = False
        self._kill_timer: QTimer | None = None

        self.ffmpeg_frame_count = 0

        self.current_qprocess: QProcess | None = None
        self.temp_dir_obj = None
        self.pal_file: Path | None = None
        self.current_step = Step.IDLE

        self.stdout_buffer = ""
        self.stderr_buffer = ""

    def _log(self, msg: str, html=False, clear_first=False):
        if html:
            self.log_html_signal.emit(msg, clear_first)
        else:
            self.log_plain_signal.emit(msg, clear_first)

    @Slot()
    def request_cancellation_slot(self):
        if self._is_cancelled:
            return
        self._is_cancelled = True
        self._log("Cancellation requested by user.")
        if self.current_qprocess and \
           self.current_qprocess.state() != QProcess.ProcessState.NotRunning:
            self._log(f"Terminating {self.current_qprocess.program()} ...")
            self.current_qprocess.terminate()

            self._kill_timer = QTimer(self)
            self._kill_timer.setInterval(100)
            self._kill_timer.setSingleShot(True)
            self._kill_timer.timeout.connect(self._force_kill_if_running)
            self._kill_timer.start()

    def _force_kill_if_running(self):
        if self.current_qprocess and \
           self.current_qprocess.state() != QProcess.ProcessState.NotRunning:
            self._log("Process didn’t die; sending SIGKILL …")
            self.current_qprocess.kill()

    def _start_next_step(self):
        if self._is_cancelled:
            self._handle_cancellation_during_step(); return
        {
            Step.PALETTE:  self._execute_palette_generation,
            Step.RENDER:   self._execute_gif_rendering,
            Step.OPTIMIZE: self._execute_gif_optimization,
            Step.FINISHED: self._finalize_conversion,
        }[self.current_step]()

    def _execute_palette_generation(self):
        self.current_step = Step.PALETTE
        self._log("\n--- Generating Palette ---")
        self._emit_weighted_progress(5, "Generating palette…")

        try:
            self.temp_dir_obj = tempfile.TemporaryDirectory()
            self.pal_file = Path(self.temp_dir_obj.name) / "palette.png"
        except Exception as e:
            self._handle_error(f"Failed to create temp directory: {e}")
            return

        self.pal_file = Path(self.temp_dir_obj.name) / "palette.png"

        filters = []
        if self.settings.fps != -1:
            filters.append(f"fps={self.settings.fps}")
        # Speed multiplier for palette generation
        if self.settings.speed_multiplier and self.settings.speed_multiplier != 1.0:
            filters.append(f"setpts=PTS/{self.settings.speed_multiplier}")
        filters.append(f"scale={self.settings.width}:{self.settings.height}:flags=lanczos")
        filters.append("format=rgb24")
        filters.append(f"palettegen=stats_mode={self.settings.palette_mode}")

        args = [
            "-v", "warning",
            "-i", str(self.settings.input_file),
            "-vf", ",".join(filters),
            "-frames:v", "1", "-update", "1",
            "-y", str(self.pal_file)
        ]

        self._run_qprocess(str(self.settings.ffmpeg_path), args, LogPrefix.FFMPEG_RENDER)

    def _execute_gif_rendering(self):
        if not self.pal_file or not self.pal_file.exists():
            self._handle_error("Palette file missing before GIF render.")
            return

        self.current_step = Step.RENDER
        self._log("\n--- Rendering GIF ---")
        self.ffmpeg_frame_count = 0

        fps = self.settings.fps
        w = self.settings.width
        h = self.settings.height

        is_webp = str(self.settings.output_file).lower().endswith(".webp")

        chain = []
        if fps != -1:
            chain.append(f"fps={fps}")
        if self.settings.speed_multiplier and self.settings.speed_multiplier != 1.0:
            chain.append(f"setpts=PTS/{self.settings.speed_multiplier}")
        chain.append(f"scale={w}:{h}:flags=lanczos")
        first_chain = ",".join(chain) + "[x]"

        filter_complex = f"{first_chain};[x][1:v]paletteuse=dither={self.settings.dither_setting}"

        args = [
            "-v", "warning",
            "-i", str(self.settings.input_file),
            "-i", str(self.pal_file),
            "-filter_complex", filter_complex,
        ]

        if self.settings.loop:
            args += ["-loop", "0"]
        else:
            args += ["-loop", "1"]

        if is_webp:
            args += ["-loop", "0"]

        args += ["-y", str(self.settings.output_file)]

        if self.settings.total_duration and self.settings.total_duration > 0:
            args = ["-progress", "pipe:1"] + args

        if self.settings.total_duration:
            self._emit_weighted_progress(0, "Rendering GIF: 0 %")
        else:
            self.progress_signal.emit(-1, "Rendering GIF…")

        self._run_qprocess(str(self.settings.ffmpeg_path), args, LogPrefix.FFMPEG_RENDER)

    def _execute_gif_optimization(self):
        is_webp = str(self.settings.output_file).lower().endswith(".webp")
        if is_webp:
            self._log("Skipping GIF optimization step for WebP output.")
            self.current_step = Step.FINISHED
            self._start_next_step()
            return

        self.current_step = Step.OPTIMIZE
        self._log("\n--- Optimising GIF ---")
        self._emit_weighted_progress(30, "Optimizing GIF…")

        args = [
            "-O3",
            "--loopcount=0" if self.settings.loop else "--no-loopcount",
            str(self.settings.output_file),
            "-o", str(self.settings.output_file)
        ]
        self._run_qprocess(str(self.settings.gifsicle_path), args, LogPrefix.GIFSICLE_OPTIMIZE)

    def _finalize_conversion(self):
        self.current_step = Step.FINISHED
        frame_info = str(self.ffmpeg_frame_count or "N/A")
        self._log(f'<br><font color="green">Generated {frame_info} frames</font>', html=True)
        self._log(f'<br><font color="green">GIF saved as:</font> '
                  f'{self.settings.output_file.resolve()}', html=True)
        self.progress_signal.emit(100, f"Done! {frame_info} frames.")
        self.finished_signal.emit(
            True, "Success",
            f"GIF saved.\nFrames: {frame_info}\n"
            f"Output: {self.settings.output_file}", False)
        self._cleanup_temp_files()

    def _run_qprocess(self, program: str, arguments: list[str], log_prefix: str):
        if self._is_cancelled:
            self._handle_cancellation_during_step(); return

        self.current_qprocess = QProcess(self)
        self.current_qprocess.setProgram(program)
        self.current_qprocess.setArguments([str(a) for a in arguments])
        self.current_qprocess.setProperty("log_prefix", log_prefix)

        self.current_qprocess.readyReadStandardOutput.connect(
            self._on_process_ready_read_stdout)
        self.current_qprocess.readyReadStandardError.connect(
            self._on_process_ready_read_stderr)
        self.current_qprocess.finished.connect(self._on_process_finished)
        self.current_qprocess.errorOccurred.connect(self._on_process_error)

        cmd_display = " ".join([program] + [str(a) for a in arguments])
        self._log(f"Cmd: {cmd_display}")

        self.current_qprocess.start()
        if not self.current_qprocess.waitForStarted(5000):
            self._handle_error(f"{log_prefix}: failed to start: "
                               f"{self.current_qprocess.errorString()}")

    @Slot()
    def _on_process_ready_read_stdout(self):
        if not self.current_qprocess:
            return
        data = self.current_qprocess.readAllStandardOutput() \
                                    .data().decode(errors="replace")
        log_prefix = self.current_qprocess.property("log_prefix")

        self.stdout_buffer += data
        for line in re.split(r'[\r\n]+', self.stdout_buffer):
            if not line:
                continue
            self._process_stdout_line(line, log_prefix)
        self.stdout_buffer = ""

    def _process_stdout_line(self, line: str, log_prefix: str):

        if log_prefix == LogPrefix.FFMPEG_RENDER:
            frame_match = re.search(r"frame=\s*(\d+)", line)
            if frame_match:
                frame = int(frame_match.group(1))
                if frame > self.ffmpeg_frame_count:
                    self.ffmpeg_frame_count = frame

            if self.settings.total_duration:
                time_match = re.search(r"out_time_ms=(\d+)", line)
                if time_match:
                    us = int(time_match.group(1))
                    pct = min(100.0, us / (self.settings.total_duration * 1_000_000.0) * 100)
                    pct_rounded = round(pct)
                    self._emit_weighted_progress(pct, f"Rendering GIF: {pct_rounded} %")
            else:

                if frame_match:
                    if self.estimated_total_frames:
                        pct = (self.ffmpeg_frame_count / self.estimated_total_frames) * 100
                        pct = min(100.0, pct)
                        pct_rounded = round(pct)
                        self._emit_weighted_progress(pct, f"Rendering GIF: {pct_rounded} %")
                    else:
                        self.progress_signal.emit(-1, f"Rendering GIF: {self.ffmpeg_frame_count} frames")

        elif log_prefix == LogPrefix.GIFSICLE_OPTIMIZE:
            if line.strip():
                self._log(f"{log_prefix}: {line.strip()}")

    @Slot()
    def _on_process_ready_read_stderr(self):
        if not self.current_qprocess:
            return
        data = self.current_qprocess.readAllStandardError() \
                                    .data().decode(errors="replace")
        log_prefix = self.current_qprocess.property("log_prefix")

        for line in re.split(r'[\r\n]+', data):
            if line:
                self._process_stderr_line(line, log_prefix)

    def _process_stderr_line(self, line: str, log_prefix: str):
        """
        spammy but harmless ffmpeg warnings we silence
        """
        ignore = [r"input frame is not in sRGB",
                  r"Last message repeated \d+ times"]
        if any(re.search(p, line, re.I) for p in ignore):
            return
        self._log(f"{log_prefix}-stderr: {line.strip()}")

    @Slot(int, QProcess.ExitStatus)
    def _on_process_finished(self, exit_code, exit_status):
        if self._kill_timer:
            self._kill_timer.stop()
            self._kill_timer.deleteLater()
            self._kill_timer = None

        if not self.current_qprocess:
            return
        log_prefix = self.current_qprocess.property("log_prefix")

        self.current_qprocess.deleteLater()
        self.current_qprocess = None

        if self._is_cancelled:
            self._handle_cancellation_during_step(); return

        if exit_status == QProcess.ExitStatus.CrashExit or exit_code != 0:
            self._handle_error(f"{log_prefix}: exited with code {exit_code}")
            return

        if self.current_step == Step.PALETTE:
            self._log(f"Palette generated → {self.pal_file}")
            self._emit_weighted_progress(100, "Palette generated.")
            self.current_step = Step.RENDER
        elif self.current_step == Step.RENDER:
            self._log("GIF rendering complete.")
            self._emit_weighted_progress(100, "GIF rendered.")
            self.current_step = Step.OPTIMIZE
        elif self.current_step == Step.OPTIMIZE:
            self._log("GIF optimisation complete.")
            self._emit_weighted_progress(100, "GIF optimised.")
            self.current_step = Step.FINISHED

        self._start_next_step()

    @Slot(QProcess.ProcessError)
    def _on_process_error(self, error):
        if self._is_cancelled and \
           error == QProcess.ProcessError.Crashed:
            self._log("Process terminated on user request.")
            self._handle_cancellation_during_step()
        else:
            self._handle_error(f"QProcess error: {error}")

    def _handle_error(self, msg: str):
        self._log(f"\nERROR: {msg}")
        self.progress_signal.emit(0, f"Error: {msg[:50]}…")
        self.finished_signal.emit(False, "Error", msg, True)
        self._cleanup_temp_files()
        self.current_step = Step.IDLE

    def _handle_cancellation_during_step(self):
        self._log("\nOperation cancelled.")
        self.progress_signal.emit(0, "Cancelled.")
        self.finished_signal.emit(False, "Cancelled",
                                  "Operation was cancelled by user.", False)
        self._cleanup_temp_files()
        self.current_step = Step.IDLE

    def _cleanup_temp_files(self):
        if self.temp_dir_obj:
            try:
                self.temp_dir_obj.cleanup()
                self._log("Temp directory cleaned up.")
            except Exception as e:
                self._log(f"Temp cleanup error: {e}")
        self.temp_dir_obj = None
        self.pal_file     = None

    def _emit_weighted_progress(self, step_progress: float, message: str):
        """
        combines step-local progress with total step weights for a smoother global progress bar
        """
        weight_done = sum(STEP_WEIGHTS[s] for s in STEP_WEIGHTS if s.value < self.current_step.value)
        weight_this = STEP_WEIGHTS.get(self.current_step, 0)
        total_progress = weight_done + (weight_this * step_progress / 100.0)
        self.progress_signal.emit(int(round(total_progress)), message)

    @Slot()
    def run_conversion(self):
        self._is_cancelled       = False
        self.ffmpeg_frame_count  = 0

        is_webp = str(self.settings.output_file).lower().endswith(".webp")
        if is_webp:
            self._log("Starting WebP generation …", clear_first=True)
            self._run_webp_conversion()
            return

        self.current_step        = Step.PALETTE
        self._log("Starting GIF generation …", clear_first=True)
        self._start_next_step()
        self.estimated_total_frames = 0

        if self.settings.total_duration:
            fps = self.settings.fps
            if fps == -1:
                fps = get_video_fps(self.settings.ffprobe_path, self.settings.input_file, log_callback=self._log)
                if fps:
                    self._log(f"Auto-detected source FPS: {fps:.2f}")
                else:
                    self._log("Could not detect source FPS.")
            if fps:
                try:
                    self.estimated_total_frames = int(self.settings.total_duration * fps)
                    self._log(f"Estimated total frames: {self.estimated_total_frames}")
                except Exception as e:
                    self._log(f"Failed to estimate total frames: {e}")

    def _run_webp_conversion(self):
        fps = self.settings.fps
        w = self.settings.width
        h = self.settings.height

        filters = []
        if fps != -1:
            filters.append(f"fps={fps}")
        if self.settings.speed_multiplier and self.settings.speed_multiplier != 1.0:
            filters.append(f"setpts=PTS/{self.settings.speed_multiplier}")
        filters.append(f"scale={w}:{h}:flags=lanczos")
        filters.append("format=rgba")

        vf_str = ",".join(filters)

        args = [
            "-v", "warning",
            "-i", str(self.settings.input_file),
            "-vf", vf_str,
            "-loop", "0",
        ]

        if self.settings.webp_lossless:
            args += ["-lossless", "1"]
        else:
            args += [
                "-q:v", str(self.settings.webp_quality),
                "-compression_level", str(self.settings.webp_compression)
            ]
        
        if self.settings.loop:
            args += ["-loop", "0"]
        else:
            args += ["-loop", "1"]

        args += ["-y", str(self.settings.output_file)]

        if self.settings.total_duration and self.settings.total_duration > 0:
            args = ["-progress", "pipe:1"] + args

        self.progress_signal.emit(0, "Rendering WebP…")

        def on_webp_finished(exit_code, exit_status):
            self.current_step = Step.FINISHED
            self._finalize_conversion()

        self.current_qprocess = QProcess(self)
        self.current_qprocess.setProgram(str(self.settings.ffmpeg_path))
        self.current_qprocess.setArguments([str(a) for a in args])
        self.current_qprocess.setProperty("log_prefix", LogPrefix.FFMPEG_RENDER)
        self.current_qprocess.finished.connect(on_webp_finished)
        self.current_qprocess.readyReadStandardOutput.connect(self._on_process_ready_read_stdout)
        self.current_qprocess.readyReadStandardError.connect(self._on_process_ready_read_stderr)
        self.current_qprocess.errorOccurred.connect(self._on_process_error)

        cmd_display = " ".join([str(self.settings.ffmpeg_path)] + [str(a) for a in args])
        self._log(f"Cmd: {cmd_display}")
        self.current_qprocess.start()

        if not self.current_qprocess.waitForStarted(5000):
            self._handle_error(f"{LogPrefix.FFMPEG_RENDER}: failed to start: "
                            f"{self.current_qprocess.errorString()}")
