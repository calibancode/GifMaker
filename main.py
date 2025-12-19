import os
import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from ui import GIFConverterApp
from utils import check_dependencies, show_error_message


if __name__ == "__main__":
    app = QApplication(sys.argv)
    # Set explicit identifiers so Wayland compositors treat this as a distinct app.
    QGuiApplication.setApplicationName("GifMaker")
    QGuiApplication.setApplicationDisplayName("GifMaker")
    QGuiApplication.setOrganizationName("GifMaker")
    QGuiApplication.setOrganizationDomain("gifmaker.local")

    # Only set a desktop file name if one exists to avoid xdg-desktop-portal registration errors.
    desktop_name = "gifmaker.desktop"
    repo_desktop = Path(__file__).resolve().parent / desktop_name
    xdg_data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    candidate_files = [repo_desktop] + [Path(d) / "applications" / desktop_name for d in xdg_data_dirs]
    for cand in candidate_files:
        if cand.is_file():
            QGuiApplication.setDesktopFileName(desktop_name)
            break

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
