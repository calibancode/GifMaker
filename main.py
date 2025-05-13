# starts app and checks dependencies
import sys
from PySide6.QtWidgets import QApplication

from ui import GIFConverterApp
from utils import check_dependencies, show_error_message

if __name__ == "__main__":
    app = QApplication(sys.argv)

    dependency_paths, missing_deps = check_dependencies()

    if missing_deps:
        show_error_message("Dependency Error",
                           "The following dependencies are missing or not in PATH:\n" +
                           "\n".join(missing_deps) +
                           "\nPlease install them and ensure they are in your system's PATH.")
        sys.exit(1)

    converter_app = GIFConverterApp(dependency_paths)
    converter_app.show()
    sys.exit(app.exec())
