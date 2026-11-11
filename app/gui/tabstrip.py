"""Полоса вкладок редактора с плавной перестановкой.

Каждая вкладка — цельный виджет (иконка + текст + крестик), поэтому при перетаскивании
соседняя вкладка плавно уезжает на новое место целиком (QPropertyAnimation по позиции).
Стандартный QTabBar с таблицей стилей рисует иконку и текст во время анимации раздельно.
"""
from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QToolButton, QWidget

from .widgets import set_icon

TAB_H = 31
GAP = 2
MAX_W = 220
ANIM_MS = 170
HYST = 10  # px: защита от перестановки туда-обратно на границе


class _Tab(QFrame):
    def __init__(self, strip: "TabStrip", key: str, icon_name: str, title: str, closable: bool):
        super().__init__(strip)
        self.strip = strip
        self.key = key
        self.closable = closable
        self.setObjectName("editorTab")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAttribute(Qt.WA_Hover, True)
        self.setProperty("selected", False)
        self.setProperty("dragging", False)
        self.setFixedHeight(TAB_H)
        self.setToolTip(title)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 8 if closable else 16, 0)
        lay.setSpacing(6)
        self.icon = QLabel()
        set_icon(self.icon, icon_name, "accent", 15)
        self.label = QLabel(title)
        self.label.setObjectName("tabTitle")
        lay.addWidget(self.icon)
        lay.addWidget(self.label)
        if closable:
            self.close_btn = QToolButton()
            self.close_btn.setObjectName("tabClose")
            self.close_btn.setFixedSize(20, 20)
            set_icon(self.close_btn, "x", "muted", 14)
            self.close_btn.setCursor(Qt.PointingHandCursor)
            self.close_btn.setToolTip("Закрыть (Ctrl+W)")
            self.close_btn.clicked.connect(lambda: strip._request_close(self))
            lay.addSpacing(2)
            lay.addWidget(self.close_btn)
        self._fit()

    def _fit(self):
        fm = self.label.fontMetrics()
        m = self.layout().contentsMargins()
        extra = m.left() + m.right() + 15 + 6 + (28 if self.closable else 0)
        text_w = min(fm.horizontalAdvance(self.label.text()) + 4, MAX_W - extra)
        self.label.setText(fm.elidedText(self.label.text(), Qt.ElideRight, text_w))
        self.setFixedWidth(text_w + extra)

    def set_flag(self, name: str, on: bool):
        if self.property(name) == on:
            return
        self.setProperty(name, on)
        for w in (self, self.label):
            w.style().unpolish(w)
            w.style().polish(w)

    # мышь: выбор, перетаскивание, закрытие средней кнопкой
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.strip._press(self, e.globalPosition().x())
        elif e.button() == Qt.MiddleButton and self.closable:
            self.strip._request_close(self)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self.strip._drag(self, e.globalPosition().x())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.strip._release(self)


class TabStrip(QWidget):
    currentChanged = Signal(int)
    tabCloseRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("tabStrip")
        self.setFixedHeight(TAB_H)
        self.tabs: list[_Tab] = []
        self._current: _Tab | None = None
        self._anims: dict[_Tab, QPropertyAnimation] = {}
        self._drag_tab: _Tab | None = None
        self._dragging = False
        self._press_x = 0.0
        self._start_x = 0
        self._last_swapped = None

    # ---------- API в стиле QTabBar ----------
    def count(self) -> int:
        return len(self.tabs)

    def currentIndex(self) -> int:
        return self.tabs.index(self._current) if self._current in self.tabs else -1

    def tabData(self, i: int):
        return self.tabs[i].key if 0 <= i < len(self.tabs) else None

    def tabText(self, i: int) -> str:
        return self.tabs[i].toolTip()

    def addTab(self, key: str, icon_name: str, title: str, closable: bool = True) -> int:
        t = _Tab(self, key, icon_name, title, closable)
        self.tabs.append(t)
        t.move(self._slot_x(len(self.tabs) - 1), 0)
        t.show()
        self._update_width()
        if self._current is None:
            self.setCurrentIndex(0)
        return len(self.tabs) - 1

    def removeTab(self, i: int):
        if not 0 <= i < len(self.tabs):
            return
        t = self.tabs.pop(i)
        was_current = t is self._current
        anim = self._anims.pop(t, None)
        if anim:
            anim.stop()
        t.hide()
        t.deleteLater()
        self._layout(animate=True)
        self._update_width()
        if was_current:
            self._current = None
            if self.tabs:
                self.setCurrentIndex(max(0, i - 1))

    def setCurrentIndex(self, i: int):
        if not 0 <= i < len(self.tabs):
            return
        t = self.tabs[i]
        if t is self._current:
            return
        if self._current in self.tabs:
            self._current.set_flag("selected", False)
        self._current = t
        t.set_flag("selected", True)
        self.currentChanged.emit(i)

    # ---------- раскладка ----------
    def _slot_x(self, i: int) -> int:
        return sum(t.width() + GAP for t in self.tabs[:i])

    def _update_width(self):
        self.setFixedWidth(max(1, self._slot_x(len(self.tabs))))

    def _animate(self, t: _Tab, x: int):
        target = QPoint(x, 0)
        old = self._anims.pop(t, None)
        if old:
            if old.state() == QPropertyAnimation.Running and old.endValue() == target:
                self._anims[t] = old  # уже едет туда, куда нужно
                return
            old.stop()
        if t.pos() == target:
            return
        a = QPropertyAnimation(t, b"pos", self)
        a.setDuration(ANIM_MS)
        a.setEasingCurve(QEasingCurve.OutCubic)
        a.setStartValue(t.pos())
        a.setEndValue(target)
        a.start()
        self._anims[t] = a

    def _layout(self, animate: bool, skip: _Tab | None = None):
        for i, t in enumerate(self.tabs):
            if t is skip:
                continue
            if animate:
                self._animate(t, self._slot_x(i))
            else:
                t.move(self._slot_x(i), 0)

    # ---------- перетаскивание ----------
    def _press(self, t: _Tab, gx: float):
        self.setCurrentIndex(self.tabs.index(t))
        self._drag_tab, self._dragging = t, False
        self._press_x, self._start_x = gx, t.x()
        self._last_swapped = None

    def _drag(self, t: _Tab, gx: float):
        if t is not self._drag_tab:
            return
        dx = gx - self._press_x
        if not self._dragging:
            if abs(dx) < QApplication.startDragDistance():
                return
            self._dragging = True
            anim = self._anims.pop(t, None)
            if anim:
                anim.stop()
            t.raise_()
            t.set_flag("dragging", True)
        x = int(max(0, min(self._start_x + dx, self.width() - t.width())))
        t.move(x, 0)

        # переставляем, когда перетаскиваемая вкладка своим краем перекрыла половину соседней:
        # влево — левый край зашёл за середину соседа, вправо — правый край зашёл за середину.
        # Пороги «туда» и «обратно» почти совпадают, поэтому обратный обмен с той же вкладкой
        # требует увести её ещё на HYST пикселей — иначе дрожание руки переставляет их туда-сюда.
        i = self.tabs.index(t)
        left, right = x, x + t.width()
        moved = False
        while i > 0:
            n = self.tabs[i - 1]
            margin = HYST if n is self._last_swapped else 0
            if left < self._slot_x(i - 1) + n.width() / 2 - margin:
                self.tabs[i - 1], self.tabs[i] = t, n
                self._last_swapped = n
                i -= 1
                moved = True
            else:
                break
        while i < len(self.tabs) - 1:
            n = self.tabs[i + 1]
            n_slot = self._slot_x(i) + t.width() + GAP
            margin = HYST if n is self._last_swapped else 0
            if right > n_slot + n.width() / 2 + margin:
                self.tabs[i + 1], self.tabs[i] = t, n
                self._last_swapped = n
                i += 1
                moved = True
            else:
                break
        if moved:
            self._layout(animate=True, skip=t)  # соседи плавно уезжают на новые места

    def _release(self, t: _Tab):
        if t is self._drag_tab and self._dragging:
            t.set_flag("dragging", False)
            self._animate(t, self._slot_x(self.tabs.index(t)))  # «докатываем» на своё место
        self._drag_tab, self._dragging = None, False

    def _request_close(self, t: _Tab):
        if t in self.tabs:
            self.tabCloseRequested.emit(self.tabs.index(t))
