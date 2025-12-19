from __future__ import annotations

import re
import tempfile
from enum import Enum, auto
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot, QTimer, QProcess

from engine import (
    LogPrefix,
    build_palette_plan,
    build_gif_render_plan,
    build_gifsicle_plan,
    build_webp_plan,
    estimate_total_frames,
    is_webp_output,
    parse_ffmpeg_progress_line,
)
from models import CommandPlan, ConversionJob, ToolPaths
from process_runner import ProcessRunner
from utils import get_video_fps


class Step(Enum):
    IDLE = auto()
    PALETTE = auto()
    RENDER = auto()
    OPTIMIZE = auto()
    FINISHED = auto()


STEP_WEIGHTS = {
    Step.PALETTE: 10,
    Step.RENDER: 70,
    Step.OPTIMIZE: 20,
}


class Worker(QObject):
    log_plain_signal = Signal(str, bool)
    log_html_signal = Signal(str, bool)
    progress_signal = Signal(int, str)
    finished_signal = Signal(bool, str, str, bool)

    def __init__(self, job: ConversionJob, tools: ToolPaths):
        super().__init__()
        self.job = job
        self.tools = tools

        self._is_cancelled = False
        self._kill_timer: QTimer | None = None
        self._is_webp = is_webp_output(self.job.output_file)
        self._render_label = "WebP" if self._is_webp else "GIF"

        self.ffmpeg_frame_count = 0
        self.estimated_total_frames: int | None = None

        self.runner = ProcessRunner(self)
        self.runner.stdout_line.connect(self._on_stdout_line)
        self.runner.stderr_line.connect(self._on_stderr_line)
        self.runner.finished.connect(self._on_process_finished)
        self.runner.error.connect(self._on_process_error)

        self.temp_dir_obj = None
        self.pal_file: Path | None = None
        self.current_step = Step.IDLE

    def _log(self, msg: str, html: bool = False, clear_first: bool = False) -> None:
        if html:
            self.log_html_signal.emit(msg, clear_first)
        else:
            self.log_plain_signal.emit(msg, clear_first)

    @Slot()
    def request_cancellation_slot(self) -> None:
        if self._is_cancelled:
            return
        self._is_cancelled = True
        self._log("Cancellation requested by user.")
        if self.runner.is_running():
            self._log("Terminating current process...")
            self.runner.terminate()

            self._kill_timer = QTimer(self)
            self._kill_timer.setInterval(100)
            self._kill_timer.setSingleShot(True)
            self._kill_timer.timeout.connect(self._force_kill_if_running)
            self._kill_timer.start()

    def _force_kill_if_running(self) -> None:
        if self.runner.is_running():
            self._log("Process did not exit; sending SIGKILL...")
            self.runner.kill()

    def _start_next_step(self) -> None:
        if self._is_cancelled:
            self._handle_cancellation_during_step()
            return
        {
            Step.PALETTE: self._execute_palette_generation,
            Step.RENDER: self._execute_rendering,
            Step.OPTIMIZE: self._execute_gif_optimization,
            Step.FINISHED: self._finalize_conversion,
        }[self.current_step]()

    def _execute_palette_generation(self) -> None:
        self.current_step = Step.PALETTE
        self._log("\n--- Generating Palette ---")
        self._emit_weighted_progress(5, "Generating palette...")

        try:
            self.temp_dir_obj = tempfile.TemporaryDirectory()
            self.pal_file = Path(self.temp_dir_obj.name) / "palette.png"
        except Exception as e:
            self._handle_error(f"Failed to create temp directory: {e}")
            return

        plan = build_palette_plan(self.job, self.tools, self.pal_file)
        self._run_plan(plan)

    def _execute_rendering(self) -> None:
        self.current_step = Step.RENDER
        self._log(f"\n--- Rendering {self._render_label} ---")
        self.ffmpeg_frame_count = 0

        if self._is_webp:
            plan = build_webp_plan(self.job, self.tools)
        else:
            if not self.pal_file or not self.pal_file.exists():
                self._handle_error("Palette file missing before GIF render.")
                return
            plan = build_gif_render_plan(self.job, self.tools, self.pal_file)

        if self.job.total_duration:
            self._emit_weighted_progress(0, f"Rendering {self._render_label}: 0 %")
        else:
            self.progress_signal.emit(-1, f"Rendering {self._render_label}...")

        self._run_plan(plan)

    def _execute_gif_optimization(self) -> None:
        if self.ffmpeg_frame_count == 1:
            self._log("Single-frame GIF detected; skipping gifsicle optimization.")
            self.current_step = Step.FINISHED
            self._start_next_step()
            return

        self.current_step = Step.OPTIMIZE
        self._log("\n--- Optimizing GIF ---")
        self._emit_weighted_progress(30, "Optimizing GIF...")

        plan = build_gifsicle_plan(self.job, self.tools)
        self._run_plan(plan)

    def _finalize_conversion(self) -> None:
        self.current_step = Step.FINISHED
        frame_info = str(self.ffmpeg_frame_count or "N/A")
        self._log(f'<br><font color="green">Generated {frame_info} frames</font>', html=True)
        self._log(
            f'<br><font color="green">{self._render_label} saved as:</font> '
            f'{self.job.output_file.resolve()}',
            html=True,
        )
        self.progress_signal.emit(100, f"Done! {frame_info} frames.")
        self.finished_signal.emit(
            True,
            "Success",
            f"{self._render_label} saved.\nFrames: {frame_info}\n"
            f"Output: {self.job.output_file}",
            False,
        )
        self._cleanup_temp_files()

    def _run_plan(self, plan: CommandPlan) -> None:
        cmd_display = " ".join([str(plan.program)] + [str(a) for a in plan.args])
        self._log(f"Cmd: {cmd_display}")

        if not self.runner.start(str(plan.program), plan.args, plan.log_prefix):
            prefix = plan.log_prefix.value if hasattr(plan.log_prefix, "value") else str(plan.log_prefix)
            self._handle_error(f"{prefix}: failed to start: {self.runner.last_error_string}")

    @Slot(str, str)
    def _on_stdout_line(self, line: str, log_prefix: str) -> None:
        if log_prefix != LogPrefix.FFMPEG_RENDER.value:
            return

        frame, out_time_ms = parse_ffmpeg_progress_line(line)
        if frame is not None and frame > self.ffmpeg_frame_count:
            self.ffmpeg_frame_count = frame

        if self.job.total_duration and out_time_ms is not None:
            pct = min(100.0, out_time_ms / (self.job.total_duration * 1_000_000.0) * 100)
            pct_rounded = round(pct)
            self._emit_weighted_progress(pct, f"Rendering {self._render_label}: {pct_rounded} %")
            return

        if frame is not None:
            if self.estimated_total_frames:
                pct = (self.ffmpeg_frame_count / self.estimated_total_frames) * 100
                pct = min(100.0, pct)
                pct_rounded = round(pct)
                self._emit_weighted_progress(pct, f"Rendering {self._render_label}: {pct_rounded} %")
            else:
                self.progress_signal.emit(-1, f"Rendering {self._render_label}: {self.ffmpeg_frame_count} frames")

    @Slot(str, str)
    def _on_stderr_line(self, line: str, log_prefix: str) -> None:
        ignore = [
            r"input frame is not in sRGB",
            r"Last message repeated \d+ times",
        ]
        if any(re.search(p, line, re.I) for p in ignore):
            return
        self._log(f"{log_prefix}-stderr: {line.strip()}")

    @Slot(int, QProcess.ExitStatus, str)
    def _on_process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus, log_prefix: str) -> None:
        if self._kill_timer:
            self._kill_timer.stop()
            self._kill_timer.deleteLater()
            self._kill_timer = None

        if self._is_cancelled:
            self._handle_cancellation_during_step()
            return

        if exit_status == QProcess.ExitStatus.CrashExit or exit_code != 0:
            self._handle_error(f"{log_prefix}: exited with code {exit_code}")
            return

        if self.current_step == Step.PALETTE:
            self._log(f"Palette generated -> {self.pal_file}")
            self._emit_weighted_progress(100, "Palette generated.")
            self.current_step = Step.RENDER
        elif self.current_step == Step.RENDER:
            self._log(f"{self._render_label} rendering complete.")
            self._emit_weighted_progress(100, f"{self._render_label} rendered.")
            if self._is_webp:
                self.current_step = Step.FINISHED
                self._start_next_step()
                return
            if self.ffmpeg_frame_count <= 1:
                self._log("Single-frame GIF detected; skipping gifsicle optimization.")
                self.current_step = Step.FINISHED
                self._start_next_step()
                return
            self.current_step = Step.OPTIMIZE
        elif self.current_step == Step.OPTIMIZE:
            self._log("GIF optimization complete.")
            self._emit_weighted_progress(100, "GIF optimized.")
            self.current_step = Step.FINISHED

        self._start_next_step()

    @Slot(QProcess.ProcessError, str)
    def _on_process_error(self, error: QProcess.ProcessError, log_prefix: str) -> None:
        if self._is_cancelled and error == QProcess.ProcessError.Crashed:
            self._log("Process terminated on user request.")
            self._handle_cancellation_during_step()
        else:
            self._handle_error(f"QProcess error: {error}")

    def _handle_error(self, msg: str) -> None:
        self._log(f"\nERROR: {msg}")
        self.progress_signal.emit(0, f"Error: {msg[:50]}...")
        self.finished_signal.emit(False, "Error", msg, True)
        self._cleanup_temp_files()
        self.current_step = Step.IDLE

    def _handle_cancellation_during_step(self) -> None:
        self._log("\nOperation cancelled.")
        self.progress_signal.emit(0, "Cancelled.")
        self.finished_signal.emit(False, "Cancelled", "Operation was cancelled by user.", False)
        self._cleanup_temp_files()
        self.current_step = Step.IDLE

    def _cleanup_temp_files(self) -> None:
        if self.temp_dir_obj:
            try:
                self.temp_dir_obj.cleanup()
                self._log("Temp directory cleaned up.")
            except Exception as e:
                self._log(f"Temp cleanup error: {e}")
        self.temp_dir_obj = None
        self.pal_file = None

    def _emit_weighted_progress(self, step_progress: float, message: str) -> None:
        if self._is_webp:
            self.progress_signal.emit(int(round(step_progress)), message)
            return

        weight_done = sum(STEP_WEIGHTS[s] for s in STEP_WEIGHTS if s.value < self.current_step.value)
        weight_this = STEP_WEIGHTS.get(self.current_step, 0)
        total_progress = weight_done + (weight_this * step_progress / 100.0)
        self.progress_signal.emit(int(round(total_progress)), message)

    @Slot()
    def run_conversion(self) -> None:
        self._is_cancelled = False
        self.ffmpeg_frame_count = 0
        self.estimated_total_frames = None

        if self.job.total_duration and self.job.total_duration > 0:
            fps = self.job.fps
            if fps == -1:
                fps = get_video_fps(self.tools.ffprobe, self.job.input_file, log_callback=self._log)
                if fps:
                    self._log(f"Auto-detected source FPS: {fps:.2f}")
                else:
                    self._log("Could not detect source FPS.")
            if fps:
                self.estimated_total_frames = estimate_total_frames(self.job.total_duration, fps)
                if self.estimated_total_frames:
                    self._log(f"Estimated total frames: {self.estimated_total_frames}")

        if self._is_webp:
            self.current_step = Step.RENDER
            self._log("Starting WebP generation ...", clear_first=True)
            self._start_next_step()
            return

        self.current_step = Step.PALETTE
        self._log("Starting GIF generation ...", clear_first=True)
        self._start_next_step()
