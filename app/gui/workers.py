from PySide6.QtCore import QThread, Signal


class Worker(QThread):
    done = Signal(object, object)  # result, error

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(), None)
        except Exception as e:  # noqa: BLE001
            self.done.emit(None, e)


_alive: set = set()


def run_bg(fn, on_done, parent=None):
    """Выполняет fn в потоке, затем вызывает on_done(result, error) в GUI-потоке."""
    w = Worker(fn, parent)
    _alive.add(w)

    def finished(res, err):
        _alive.discard(w)
        on_done(res, err)

    w.done.connect(finished)
    w.start()
