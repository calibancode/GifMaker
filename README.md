# Video to GIF/WebP Converter

PySide6 frontend for converting video clips to GIF or animated WebP with ffmpeg & gifsicle.

## Requirements

* Python 3.10+
* `ffmpeg`, `ffprobe`, `gifsicle` in `$PATH`
* `pip install PySide6`

## Usage
```bash
python main.py
```

## Features

* Input: `.mp4`, `.mkv`, `.webm`, etc.
* Output: `.gif` or `.webp`
* Adjustable FPS, resolution, speed
* Palette and dithering options
* WebP quality, compression, lossless toggle
* Drag-and-drop, cancel button, logging

## License

GPLv3
