"""Модальные окна внутри главного окна: затемнение фона + карточка по центру (вместо отдельных окон)."""
from typing import Callable, Optional

from PySide6.QtCore import QEvent, QEventLoop, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QComboBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QSpinBox, QToolButton, QVBoxLayout, QWidget)

from .icons import icon
from .theme import C


class Modal(QWidget):
    active = 0

    def __init__(self, host: QWidget, title: str, body: QWidget, ok_text: str = "Сохранить",
                 cancel_text: Optional[str] = "Отмена", danger: bool = False, width: int = 520,
                 validate: Optional[Callable[[], Optional[str]]] = None):
        super().__init__(host)
        self.host = host
        self.validate = validate
        self.result = False
        self._loop: Optional[QEventLoop] = None

        self.card = QFrame(self)
        self.card.setObjectName("modalCard")
        self.card.setFixedWidth(width)
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 170))
        self.card.setGraphicsEffect(shadow)

        lay = QVBoxLayout(self.card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QFrame()
        head.setObjectName("modalHead")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(20, 12, 10, 12)
        t = QLabel(title)
        t.setObjectName("modalTitle")
        close = QToolButton()
        close.setIcon(icon("x", C["muted"], 16))
        close.setIconSize(QSize(16, 16))
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(lambda: self.done(False))
        hl.addWidget(t)
        hl.addStretch()
        hl.addWidget(close)
        lay.addWidget(head)

        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(20, 16, 20, 14)
        bl.setSpacing(10)
        bl.addWidget(body)
        self.error = QLabel()
        self.error.setObjectName("modalError")
        self.error.setWordWrap(True)
        self.error.hide()
        bl.addWidget(self.error)
        lay.addWidget(box)

        foot = QFrame()
        foot.setObjectName("modalFoot")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(20, 12, 20, 12)
        fl.setSpacing(8)
        fl.addStretch()
        if cancel_text:
            cancel = QPushButton(cancel_text)
            cancel.setCursor(Qt.PointingHandCursor)
            cancel.clicked.connect(lambda: self.done(False))
            fl.addWidget(cancel)
        self.ok = QPushButton(ok_text)
        self.ok.setObjectName("dangerBtn" if danger else "primary")
        self.ok.setCursor(Qt.PointingHandCursor)
        self.ok.clicked.connect(self._accept)
        fl.addWidget(self.ok)
        lay.addWidget(foot)
        self.body = body
        self.hide()

    # ---------- геометрия и отрисовка ----------
    def _place(self):
        self.setGeometry(self.host.rect())
        self.card.adjustSize()
        cw, ch = self.card.width(), self.card.sizeHint().height()
        x = (self.width() - cw) // 2
        y = max(30, (self.height() - ch) // 2 - 40)
        self.card.setGeometry(x, y, cw, ch)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, C['scrim']))

    def eventFilter(self, obj, ev):
        if obj is self.host and ev.type() == QEvent.Resize:
            self._place()
        return False

    def mousePressEvent(self, e):
        e.accept()  # клики по затемнению не проходят к окну

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.done(False)
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._accept()
        else:
            super().keyPressEvent(e)

    # ---------- результат ----------
    def _accept(self):
        if self.validate:
            err = self.validate()
            if err:
                self.error.setText(err)
                self.error.show()
                self._place()
                return
        self.done(True)

    def done(self, ok: bool):
        self.result = ok
        if self._loop:
            self._loop.quit()

    def open(self):
        """Показать без блокировки (используется exec() и для скриншотов)."""
        central = getattr(self.host, "centralWidget", lambda: None)()
        if central:
            central.setEnabled(False)
        Modal.active += 1
        self.host.installEventFilter(self)
        self._place()
        self.show()
        self.raise_()
        for w in self.body.findChildren(QWidget):
            if isinstance(w, (QLineEdit, QPlainTextEdit, QComboBox, QSpinBox)) and w.isEnabled() and w.isVisible():
                w.setFocus()
                break
        else:
            self.ok.setFocus()

    def close_modal(self):
        central = getattr(self.host, "centralWidget", lambda: None)()
        Modal.active -= 1
        if central and Modal.active == 0:
            central.setEnabled(True)
        self.host.removeEventFilter(self)
        self.hide()
        self.deleteLater()

    def exec(self) -> bool:
        self.open()
        self._loop = QEventLoop()
        self._loop.exec()
        self.close_modal()
        return self.result


# ---------- готовые диалоги ----------
def _text(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("modalText")
    lbl.setWordWrap(True)
    return lbl


def confirm(host, title: str, text: str, ok_text: str = "Удалить", danger: bool = True) -> bool:
    return Modal(host, title, _text(text), ok_text=ok_text, danger=danger, width=440).exec()


def alert(host, title: str, text: str):
    Modal(host, title, _text(text), ok_text="OK", cancel_text=None, width=440).exec()


def ask_int(host, title: str, label: str, value: int, lo: int, hi: int) -> Optional[int]:
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(8)
    v.addWidget(_text(label))
    spin = QSpinBox()
    spin.setRange(lo, hi)
    spin.setValue(value)
    v.addWidget(spin)
    return spin.value() if Modal(host, title, w, ok_text="Создать", width=420).exec() else None
