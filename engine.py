from __future__ import annotations

from enum import Enum
from pathlib import Path
import re
from typing import Optional

from models import CommandPlan, ConversionJob, ToolPaths


class LogPrefix(str, Enum):
    FFMPEG_RENDER = "ffmpeg-render"
    FFMPEG_PALETTE = "ffmpeg-palette"
    GIFSICLE_OPTIMIZE = "gifsicle-optimize"


def is_webp_output(output_path: Path) -> bool:
    return str(output_path).lower().endswith(".webp")


def build_palette_plan(job: ConversionJob, tools: ToolPaths, palette_file: Path) -> CommandPlan:
    filters = _base_filters(job)
    filters.append("format=rgb24")
    filters.append(f"palettegen=stats_mode={job.palette_mode}")

    args = [
        "-v", "warning",
        "-i", str(job.input_file),
        "-vf", ",".join(filters),
        "-update", "1",
        "-y", str(palette_file)
    ]

    return CommandPlan(program=tools.ffmpeg, args=args, log_prefix=LogPrefix.FFMPEG_PALETTE)


def build_gif_render_plan(job: ConversionJob, tools: ToolPaths, palette_file: Path) -> CommandPlan:
    chain = _base_filters(job)
    if chain:
        first_chain = f"[0:v]{','.join(chain)}[x]"
        filter_complex = f"{first_chain};[x][1:v]paletteuse=dither={job.dither_setting}"
    else:
        filter_complex = f"[0:v][1:v]paletteuse=dither={job.dither_setting}"

    args = [
        "-v", "warning",
        "-i", str(job.input_file),
        "-i", str(palette_file),
        "-filter_complex", filter_complex,
    ]

    args += ["-loop", "0" if job.loop else "1"]
    args += ["-y", str(job.output_file)]

    if job.total_duration and job.total_duration > 0:
        args = ["-progress", "pipe:1"] + args

    return CommandPlan(program=tools.ffmpeg, args=args, log_prefix=LogPrefix.FFMPEG_RENDER)


def build_gifsicle_plan(job: ConversionJob, tools: ToolPaths) -> CommandPlan:
    args = [
        "-O3",
        "--loopcount=0" if job.loop else "--no-loopcount",
        str(job.output_file),
        "-o", str(job.output_file)
    ]
    return CommandPlan(program=tools.gifsicle, args=args, log_prefix=LogPrefix.GIFSICLE_OPTIMIZE)


def build_webp_plan(job: ConversionJob, tools: ToolPaths) -> CommandPlan:
    filters = _base_filters(job)
    filters.append("format=rgba")

    args = [
        "-v", "warning",
        "-i", str(job.input_file),
        "-vf", ",".join(filters),
    ]

    if job.webp_lossless:
        args += ["-lossless", "1"]
    else:
        args += [
            "-q:v", str(job.webp_quality),
            "-compression_level", str(job.webp_compression)
        ]

    args += ["-loop", "0" if job.loop else "1"]
    args += ["-y", str(job.output_file)]

    if job.total_duration and job.total_duration > 0:
        args = ["-progress", "pipe:1"] + args

    return CommandPlan(program=tools.ffmpeg, args=args, log_prefix=LogPrefix.FFMPEG_RENDER)


def parse_ffmpeg_progress_line(line: str) -> tuple[Optional[int], Optional[int]]:
    frame_match = re.search(r"frame=\s*(\d+)", line)
    time_match = re.search(r"out_time_ms=(\d+)", line)

    frame = int(frame_match.group(1)) if frame_match else None
    out_time_ms = int(time_match.group(1)) if time_match else None

    return frame, out_time_ms


def estimate_total_frames(duration: Optional[float], fps: Optional[float]) -> Optional[int]:
    if not duration or not fps:
        return None
    return int(duration * fps)


def _base_filters(job: ConversionJob) -> list[str]:
    filters = []
    if job.fps != -1:
        filters.append(f"fps={job.fps}")
    if job.speed_multiplier and job.speed_multiplier != 1.0:
        filters.append(f"setpts=PTS/{job.speed_multiplier}")
    _add_scale_crop(filters, job.width, job.height)
    return filters


def _add_scale_crop(filters: list[str], width: int, height: int) -> None:
    if width == -1 and height == -1:
        return
    if width == -1 or height == -1:
        filters.append(f"scale={width if width != -1 else -1}:{height if height != -1 else -1}:flags=lanczos")
        return
    filters += [
        f"scale={width}:{height}:flags=lanczos:force_original_aspect_ratio=increase",
        f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2"
    ]
