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


class _SectionHeader(QFrame):
    """Заголовок раздела боковой панели. Клик (по стрелке или названию) сворачивает/разворачивает раздел;
    состояние запоминается в настройках."""

    def __init__(self, title: str, target: QWidget | None, key: str):
        super().__init__()
        self.target, self.key = target, key
        self.setObjectName("sectionHeader")
        self.setFixedHeight(26)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 8, 0)
        lay.setSpacing(2)
        self.chev = QLabel()
        t = QLabel(title)
        t.setObjectName("sectionTitle")
        lay.addWidget(self.chev)
        lay.addWidget(t)
        lay.addStretch()
        if target is not None:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("Свернуть / развернуть")
            from .. import settings
            self.set_open(key not in (settings.get("collapsed_sections") or []), save=False)
        else:
            set_icon(self.chev, "chevron", "text", 14)

    def set_open(self, on: bool, save: bool = True):
        self.target.setVisible(on)
        set_icon(self.chev, "chevron" if on else "chevron_right", "text", 14)
        if save:
            from .. import settings
            cur = set(settings.get("collapsed_sections") or [])
            cur.discard(self.key) if on else cur.add(self.key)
            settings.set("collapsed_sections", sorted(cur))

    def mousePressEvent(self, e):
        if self.target is not None and e.button() == Qt.LeftButton:
            self.set_open(not self.target.isVisible())
            e.accept()
            return
        super().mousePressEvent(e)


def section(title: str, target: QWidget | None = None, key: str = "") -> QFrame:
    """Заголовок раздела. С target — сворачиваемый: клик скрывает/показывает target."""
    return _SectionHeader(title, target, key or title)


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


def restore_selection(table, rows) -> None:
    """Выделить строки rows одним вызовом. selectRow() в режиме множественного выбора сбрасывает остальные
    строки — при автообновлении таблицы от нескольких выбранных оставалась одна."""
    from PySide6.QtCore import QItemSelection, QItemSelectionModel
    sel = QItemSelection()
    last = table.columnCount() - 1
    for r in rows:
        sel.select(table.model().index(r, 0), table.model().index(r, last))
    table.selectionModel().select(sel, QItemSelectionModel.ClearAndSelect)
