from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from utils import WEBP_QUALITY, WEBP_COMPRESSION


@dataclass(frozen=True)
class ToolPaths:
    ffmpeg: Path
    ffprobe: Path
    gifsicle: Path


@dataclass
class ConversionJob:
    input_file: Path
    output_file: Path
    fps: int
    width: int
    height: int
    dither_setting: str
    speed_multiplier: float = 1.0
    palette_mode: str = "diff"
    webp_quality: int = WEBP_QUALITY
    webp_compression: int = WEBP_COMPRESSION
    webp_lossless: bool = False
    loop: bool = True
    total_duration: Optional[float] = None


@dataclass(frozen=True)
class CommandPlan:
    program: Path
    args: list[str]
    log_prefix: str
