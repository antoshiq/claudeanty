"""Общие элементы интерфейса."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QToolButton, QWidget)

from .icons import icon
from .theme import C


def set_icon(w, name: str, color_key: str = "text", size: int = 16):
    """Иконка, которая перекрашивается при смене темы (см. retint)."""
    w.setProperty("ico", name)
    w.setProperty("ico_color", color_key)
    w.setProperty("ico_size", size)
    if isinstance(w, QLabel):
        w.setPixmap(icon(name, C.get(color_key, color_key), size).pixmap(size, size))
    else:
        w.setIcon(icon(name, C.get(color_key, color_key), size))
        w.setIconSize(QSize(size, size))


def retint(root: QWidget):
    for w in root.findChildren(QWidget):
        name = w.property("ico")
        if name and isinstance(w, (QAbstractButton, QLabel)):
            set_icon(w, name, w.property("ico_color"), w.property("ico_size"))


def tool(name: str, tip: str, fn, size: int = 16) -> QToolButton:
    b = QToolButton()
    set_icon(b, name, "text", size)
    b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    b.clicked.connect(fn)
    return b


def side_title(text: str, *tools) -> QWidget:
    w = QWidget()
    w.setFixedHeight(35)
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 8, 0)
    lay.setSpacing(2)
    lbl = QLabel(text)
    lbl.setObjectName("sideTitle")
    lay.addWidget(lbl)
    lay.addStretch()
    for t in tools:
        lay.addWidget(t)
    return w


def section(title: str) -> QFrame:
    head = QFrame()
    head.setObjectName("sectionHeader")
    head.setFixedHeight(26)
    lay = QHBoxLayout(head)
    lay.setContentsMargins(4, 0, 8, 0)
    lay.setSpacing(2)
    chev = QLabel()
    set_icon(chev, "chevron", "text", 14)
    t = QLabel(title)
    t.setObjectName("sectionTitle")
    lay.addWidget(chev)
    lay.addWidget(t)
    lay.addStretch()
    return head


def side_list() -> QListWidget:
    lst = QListWidget()
    lst.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    lst.setTextElideMode(Qt.ElideRight)
    lst.setIconSize(QSize(16, 16))
    return lst


def dot_icon(color: str, size: int = 12):
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    pa = QPainter(pm)
    pa.setRenderHint(QPainter.Antialiasing)
    pa.setBrush(QColor(color))
    pa.setPen(Qt.NoPen)
    pa.drawEllipse(size // 2, size // 2, size, size)
    pa.end()
    pm.setDevicePixelRatio(2)
    return QIcon(pm)


def fill_list(lst: QListWidget, entries, selected) -> None:
    """entries: [(текст, значение, имя_иконки или #цвет)]; selected — значение, которое нужно выделить."""
    lst.blockSignals(True)
    lst.clear()
    row = -1
    for i, (label, val, ico) in enumerate(entries):
        it = QListWidgetItem(dot_icon(ico) if ico.startswith("#") else icon(ico, C["muted"], 16), label)
        it.setData(Qt.UserRole, val)
        lst.addItem(it)
        if val == selected:
            row = i
    if row >= 0:
        lst.setCurrentRow(row)
    else:
        lst.clearSelection()
    lst.blockSignals(False)


def empty_label(text: str = "") -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("emptyState")
    lbl.setAlignment(Qt.AlignCenter)
    return lbl
