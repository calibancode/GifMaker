from pathlib import Path

from engine import (
    build_gif_render_plan,
    build_gifsicle_plan,
    build_palette_plan,
    build_webp_plan,
    estimate_total_frames,
    is_webp_output,
    parse_ffmpeg_progress_line,
)
from models import ConversionJob, ToolPaths


def _job(**overrides):
    data = dict(
        input_file=Path("/tmp/input.mp4"),
        output_file=Path("/tmp/out.gif"),
        fps=15,
        width=480,
        height=-1,
        dither_setting="floyd_steinberg",
        speed_multiplier=1.0,
        palette_mode="diff",
        webp_quality=90,
        webp_compression=4,
        webp_lossless=False,
        loop=True,
        total_duration=12.5,
    )
    data.update(overrides)
    return ConversionJob(**data)


def _tools():
    return ToolPaths(
        ffmpeg=Path("/usr/bin/ffmpeg"),
        ffprobe=Path("/usr/bin/ffprobe"),
        gifsicle=Path("/usr/bin/gifsicle"),
    )


def test_build_gif_render_plan_with_filters():
    job = _job()
    tools = _tools()
    plan = build_gif_render_plan(job, tools, Path("/tmp/palette.png"))

    assert "-filter_complex" in plan.args
    flt = plan.args[plan.args.index("-filter_complex") + 1]
    assert "fps=15" in flt
    assert "scale=480:-1:flags=lanczos" in flt
    assert "paletteuse=dither=floyd_steinberg" in flt


def test_build_gif_render_plan_no_filters():
    job = _job(fps=-1, width=-1, height=-1)
    tools = _tools()
    plan = build_gif_render_plan(job, tools, Path("/tmp/palette.png"))

    flt = plan.args[plan.args.index("-filter_complex") + 1]
    assert flt == "[0:v][1:v]paletteuse=dither=floyd_steinberg"


def test_build_webp_plan_lossless():
    job = _job(output_file=Path("/tmp/out.webp"), webp_lossless=True)
    tools = _tools()
    plan = build_webp_plan(job, tools)

    assert "-lossless" in plan.args
    assert "-q:v" not in plan.args


def test_build_webp_plan_lossy_includes_quality():
    job = _job(output_file=Path("/tmp/out.webp"), webp_lossless=False)
    tools = _tools()
    plan = build_webp_plan(job, tools)

    assert "-lossless" not in plan.args
    assert "-q:v" in plan.args
    assert "-compression_level" in plan.args


def test_build_palette_plan_includes_palettegen():
    job = _job(palette_mode="diff")
    tools = _tools()
    plan = build_palette_plan(job, tools, Path("/tmp/palette.png"))

    assert "palettegen=stats_mode=diff" in " ".join(plan.args)


def test_build_gifsicle_plan_loop():
    job = _job(loop=True)
    tools = _tools()
    plan = build_gifsicle_plan(job, tools)

    assert "--loopcount=0" in plan.args


def test_is_webp_output():
    assert is_webp_output(Path("/tmp/out.webp")) is True
    assert is_webp_output(Path("/tmp/out.gif")) is False


def test_parse_ffmpeg_progress_line():
    line = "frame=  12\nout_time_ms=123456"
    frame, out_time_ms = parse_ffmpeg_progress_line(line)
    assert frame == 12
    assert out_time_ms == 123456


def test_parse_ffmpeg_progress_line_missing_fields():
    frame, out_time_ms = parse_ffmpeg_progress_line("bitrate=1000")
    assert frame is None
    assert out_time_ms is None


def test_estimate_total_frames():
    assert estimate_total_frames(10.0, 24.0) == 240
    assert estimate_total_frames(None, 24.0) is None
    assert estimate_total_frames(10.0, None) is None
