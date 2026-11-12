import sys


def _frozen_stdio():
    """Собранная версия без консоли: sys.stdout/stderr = None, а uvicorn и logging пишут туда —
    перенаправляем в data/logs/app.log (последний запуск)."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from app.paths import DATA_DIR
    logs = DATA_DIR / "logs"
    logs.mkdir(exist_ok=True)
    f = open(logs / "app.log", "w", encoding="utf-8", buffering=1)  # noqa: SIM115
    sys.stdout = sys.stdout or f
    sys.stderr = sys.stderr or f


_frozen_stdio()

from PySide6.QtWidgets import QApplication  # noqa: E402

from app import settings  # noqa: E402
from app.api.server import start_api_thread  # noqa: E402
from app.gui.main_window import MainWindow  # noqa: E402
from app.gui.theme import apply_theme  # noqa: E402


def main():
    if settings.get("api_enabled"):
        start_api_thread()
    qt = QApplication(sys.argv)
    apply_theme(qt)
    win = MainWindow()
    win.show()
    from app.manager import manager
    manager.scheduler.start()
    sys.exit(qt.exec())


if __name__ == "__main__":
    main()
