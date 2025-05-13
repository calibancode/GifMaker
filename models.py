# defines data structure for converter settings
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from utils import WEBP_QUALITY, WEBP_COMPRESSION

@dataclass
class ConverterSettings:
    input_file:       Path
    output_file:      Path
    fps:              int
    width:            int
    height:           int
    dither_setting:   str
    ffmpeg_path:      Path
    ffprobe_path:     Path
    gifsicle_path:    Path
    total_duration:   Optional[float]
    speed_multiplier: float = 1.0
    webp_quality: int = WEBP_QUALITY
    webp_compression: int = WEBP_COMPRESSION
    webp_lossless: bool = False
    loop: bool = True
    palette_mode: str = "diff"