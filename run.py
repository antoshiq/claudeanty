import sys

from PySide6.QtWidgets import QApplication

from app import settings
from app.api.server import start_api_thread
from app.gui.main_window import MainWindow
from app.gui.theme import apply_theme


def main():
    if settings.get("api_enabled"):
        start_api_thread()
    qt = QApplication(sys.argv)
    apply_theme(qt)
    win = MainWindow()
    win.show()
    sys.exit(qt.exec())


if __name__ == "__main__":
    main()
