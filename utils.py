# holds utility functions for shared logic
import shutil
import subprocess
import os
from pathlib import Path
from typing import Tuple, Union, Optional

from PySide6.QtWidgets import QMessageBox

# Hardcoded configuration values (config.ini deprecated)
DEFAULT_FPS     = -1
DEFAULT_WIDTH   = -1
DEFAULT_HEIGHT  = -1
DEFAULT_TIMEOUT = 10
DEFAULT_SPEED = 1.0

MAX_WIDTH  = 2048
MAX_HEIGHT = 2048
MAX_FPS    = 60
MAX_SPEED = 10.0

DITHER_OPTIONS_FULL = {
    "None":        ("No dithering (smallest file, lowest quality)", "none"),
    "Smooth":      ("Floyd–Steinberg (next-door error diffusion, large)", "floyd_steinberg"),
    "Patterned":   ("Bayer 5x5 (deterministic, blocky, small)", "bayer:bayer_scale=5"),
    "Sierra2_4a":     ("Sierra2 Lite (neighbor error diffusion, largest)", "sierra2_4a"),
}
DEFAULT_DITHER_SHORT_KEY = "Smooth"

PALETTE_MODE_OPTIONS = {
    "Single": ("Single (static palette, worst quality)", "single"),
    "Diff":   ("Diff (prioritizes frame-to-frame changes, best for motion)", "diff"),
    "Full":   ("Full (all pixels equally, most representative)", "full")
}
DEFAULT_PALETTE_MODE_KEY = "Full"

WEBP_QUALITY = 90
WEBP_COMPRESSION = 4

def check_dependencies() -> Tuple[dict, list]:
    paths = {}
    missing = []

    for name in ("ffmpeg", "ffprobe", "gifsicle"):
        which_result = shutil.which(name)
        if which_result:
            paths[name] = (which_result, "PATH")
        else:
            missing.append(name)

    return paths, missing

def _path(obj: Union[str, Path]) -> Path:
    return obj if isinstance(obj, Path) else Path(obj)

def validate_output_path(output_path: Union[str, Path]) -> Tuple[bool, str]:
    p = _path(output_path)

    if not p.name:
        return False, "Output filename cannot be empty."

    if p.is_dir():
        return False, f"Output path is a directory:\n{p}"

    if not p.parent.exists():
        return False, f"Output directory does not exist:\n{p.parent}"

    if not os.access(p.parent, os.W_OK):
        return False, f"Output directory is not writeable:\n{p.parent}"

    bad_chars = set('<>:"/\\|?*')
    if any(ch in bad_chars for ch in p.name):
        return False, f"Output filename contains invalid characters:\n{p.name}"

    return True, ""

def get_video_duration(ffprobe_path: Union[str, Path],
                       input_file:   Union[str, Path],
                       log_callback=None) -> Optional[float]:

    ffprobe_path = str(ffprobe_path) if ffprobe_path else None
    inp = _path(input_file)

    if not ffprobe_path or not inp.is_file():
        if log_callback:
            log_callback("ffprobe path or input invalid for duration check.", False)
        return None

    try:
        size_mb = inp.stat().st_size / (1024 * 1024)
    except OSError:
        size_mb = 0
    # Adjust timeout based on file size (~5s + 1s per 20MB, max 60s). Probably useless.
    timeout = min(60, max(DEFAULT_TIMEOUT, int(size_mb // 20 + 5)))

    cmd = [
        ffprobe_path, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(inp)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True,
                                text=True, check=True, timeout=timeout)
        return float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError) as e:
        if log_callback:
            log_callback(f"ffprobe failure: {e}", False)
        return None

def get_video_fps(ffprobe_path: Union[str, Path], input_file: Union[str, Path], log_callback=None) -> Optional[float]:
    inp = _path(input_file)
    ffprobe_path = str(ffprobe_path)
    cmd = [
        ffprobe_path,
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "default=nokey=1:noprint_wrappers=1",
        str(inp)
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
        fps_str = result.stdout.strip()
        num, denom = map(int, fps_str.split('/'))
        return num / denom if denom != 0 else None
    except Exception as e:
        if log_callback:
            log_callback(f"ffprobe FPS check failed: {e}", False)
        return None

def show_error_message(title, message, parent=None):
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title)
    box.setText(message)
    box.exec()
