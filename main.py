# starts app and checks dependencies
import sys
from pathlib import Path
import argparse

from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication

from ui import GIFConverterApp
from utils import (
    check_dependencies,
    show_error_message,
    validate_output_path,
    get_video_duration,
    DEFAULT_FPS, DEFAULT_WIDTH, DEFAULT_HEIGHT, DEFAULT_SPEED,
    DITHER_OPTIONS_FULL, DEFAULT_DITHER_SHORT_KEY,
    PALETTE_MODE_OPTIONS, DEFAULT_PALETTE_MODE_KEY,
    WEBP_QUALITY, WEBP_COMPRESSION
)
from models import ConverterSettings
from worker import Worker


def run_cli_mode(args: list[str]) -> None:
    parser = argparse.ArgumentParser(
        prog="gifmaker",
        description="Headless GIF/WebP converter (uses the same engine as the GUI).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    parser.add_argument("-i", "--input", required=True, help="Input video file")
    parser.add_argument("-o", "--output", required=True, help="Output GIF/WebP file")
    parser.add_argument("-fps", type=int, default=DEFAULT_FPS, help="Frames per second (-1 = source)")
    parser.add_argument("-w", "--width", type=int, default=DEFAULT_WIDTH, help="Output width (-1 = auto)")
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help="Output height (-1 = auto)")
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED, help="Speed multiplier (0.1–10)")

    parser.add_argument(
        "--dither",
        choices=[v[1] for v in DITHER_OPTIONS_FULL.values()],
        default=DITHER_OPTIONS_FULL[DEFAULT_DITHER_SHORT_KEY][1],
        help="Dithering algorithm"
    )
    parser.add_argument(
        "--palette",
        choices=[v[1] for v in PALETTE_MODE_OPTIONS.values()],
        default=PALETTE_MODE_OPTIONS[DEFAULT_PALETTE_MODE_KEY][1],
        help="Palette stats mode"
    )
    parser.add_argument("--loop", action="store_true", default=False, help="Force infinite loop")
    parser.add_argument("--no-loop", dest="loop", action="store_false", help="Disable looping")

    parser.add_argument("--webp-lossless", action="store_true", help="Encode lossless WebP")
    parser.add_argument("--webp-quality", type=int, default=WEBP_QUALITY, help="WebP quality (0-100)")
    parser.add_argument("--webp-compression", type=int, default=WEBP_COMPRESSION, help="WebP compression level (0-6)")

    ns = parser.parse_args(args)

    dependency_paths, missing = check_dependencies()
    if missing:
        print("Missing dependencies:", ", ".join(missing), file=sys.stderr)
        sys.exit(1)

    inp = Path(ns.input)
    out = Path(ns.output)

    if not inp.is_file():
        print(f"Error: Input file does not exist: {inp}", file=sys.stderr)
        sys.exit(1)

    ok, msg = validate_output_path(out)
    if not ok:
        print("Error:", msg, file=sys.stderr)
        sys.exit(1)

    duration = get_video_duration(dependency_paths["ffprobe"][0], inp)

    settings = ConverterSettings(
        input_file=inp,
        output_file=out,
        fps=ns.fps,
        width=ns.width,
        height=ns.height,
        dither_setting=ns.dither,
        ffmpeg_path=Path(dependency_paths["ffmpeg"][0]),
        ffprobe_path=Path(dependency_paths["ffprobe"][0]),
        gifsicle_path=Path(dependency_paths["gifsicle"][0]),
        total_duration=duration,
        speed_multiplier=ns.speed,
        webp_quality=ns.webp_quality,
        webp_compression=ns.webp_compression,
        webp_lossless=ns.webp_lossless,
        loop=ns.loop,
        palette_mode=ns.palette
    )

    app = QCoreApplication(sys.argv)
    worker = Worker(settings)

    worker.log_plain_signal.connect(lambda m, *_: print(m))
    worker.progress_signal.connect(lambda v, t: print(t))
    worker.finished_signal.connect(lambda success, *_: app.quit() if success else sys.exit(1))

    QTimer.singleShot(0, worker.run_conversion)
    sys.exit(app.exec())


if __name__ == "__main__":
    cli_flags = {
        "-i", "--input", "-o", "--output", "-fps", "-w", "--width",
        "-h", "--height", "--speed", "--dither", "--palette",
        "--loop", "--no-loop", "--webp-lossless", "--webp-quality", "--webp-compression"
    }

    if any(arg in cli_flags for arg in sys.argv[1:]):
        run_cli_mode(sys.argv[1:])
    else:
        app = QApplication(sys.argv)

        dependency_paths, missing_deps = check_dependencies()
        if missing_deps:
            show_error_message(
                "Dependency Error",
                "The following dependencies are missing or not in PATH:\n"
                + "\n".join(missing_deps)
                + "\nPlease install them and ensure they are in your system's PATH."
            )
            sys.exit(1)

        converter_app = GIFConverterApp(dependency_paths)
        converter_app.show()
        sys.exit(app.exec())