from __future__ import annotations

import re

from PySide6.QtCore import QObject, Signal, Slot, QProcess


class ProcessRunner(QObject):
    stdout_line = Signal(str, str)
    stderr_line = Signal(str, str)
    finished = Signal(int, QProcess.ExitStatus, str)
    error = Signal(QProcess.ProcessError, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: QProcess | None = None
        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._log_prefix = ""
        self._last_error = ""

    @property
    def last_error_string(self) -> str:
        return self._last_error

    def is_running(self) -> bool:
        return bool(self._process and self._process.state() != QProcess.ProcessState.NotRunning)

    def start(self, program: str, arguments: list[str], log_prefix: str) -> bool:
        if self.is_running():
            self._last_error = "Process already running"
            return False

        self._stdout_buffer = ""
        self._stderr_buffer = ""
        self._log_prefix = log_prefix.value if hasattr(log_prefix, "value") else str(log_prefix)

        self._process = QProcess(self)
        self._process.setProgram(program)
        self._process.setArguments([str(a) for a in arguments])

        self._process.readyReadStandardOutput.connect(self._on_ready_read_stdout)
        self._process.readyReadStandardError.connect(self._on_ready_read_stderr)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)

        self._process.start()
        if not self._process.waitForStarted(5000):
            self._last_error = self._process.errorString()
            self._process.deleteLater()
            self._process = None
            return False

        return True

    def terminate(self) -> None:
        if self.is_running():
            self._process.terminate()

    def kill(self) -> None:
        if self.is_running():
            self._process.kill()

    @Slot()
    def _on_ready_read_stdout(self) -> None:
        if not self._process:
            return
        data = self._process.readAllStandardOutput().data().decode(errors="replace")
        self._stdout_buffer += data

        parts = re.split(r"[\r\n]+", self._stdout_buffer)
        self._stdout_buffer = parts[-1] if self._stdout_buffer and self._stdout_buffer[-1] not in "\r\n" else ""
        lines_to_process = parts[:-1] if self._stdout_buffer else parts
        for line in lines_to_process:
            if line:
                self.stdout_line.emit(line, self._log_prefix)

    @Slot()
    def _on_ready_read_stderr(self) -> None:
        if not self._process:
            return
        data = self._process.readAllStandardError().data().decode(errors="replace")
        self._stderr_buffer += data

        parts = re.split(r"[\r\n]+", self._stderr_buffer)
        self._stderr_buffer = parts[-1] if self._stderr_buffer and self._stderr_buffer[-1] not in "\r\n" else ""
        lines_to_process = parts[:-1] if self._stderr_buffer else parts
        for line in lines_to_process:
            if line:
                self.stderr_line.emit(line, self._log_prefix)

    @Slot(int, QProcess.ExitStatus)
    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        log_prefix = self._log_prefix
        if self._process:
            self._process.deleteLater()
            self._process = None
        self.finished.emit(exit_code, exit_status, log_prefix)

    @Slot(QProcess.ProcessError)
    def _on_error(self, error: QProcess.ProcessError) -> None:
        log_prefix = self._log_prefix
        self.error.emit(error, log_prefix)
