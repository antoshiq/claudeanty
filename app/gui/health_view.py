"""Окно «Здоровье профилей»: график ✓ / ! / ✗ по датам и последние проверки с отметкой «стало хуже»."""
import datetime as dt

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QToolTip, QVBoxLayout,
                               QWidget)

from .. import db, health
from .modal import Modal
from .theme import C

_STATES = (("ok", "✓ в порядке"), ("warn", "! внимание"), ("fail", "✗ проблема"))


def _colors() -> dict:
    light = QColor(C["editor"]).lightness() > 128
    return {"ok": QColor(C["green"]), "warn": QColor("#9a6700" if light else "#d29922"), "fail": QColor(C["red"])}


class HealthChart(QWidget):
    """Столбцы по дням, сложенные из ✓ / ! / ✗ (снизу вверх). Наведение — подсказка с числами."""

    def __init__(self):
        super().__init__()
        self.days: list[dict] = []
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self._bars: list[tuple[QRectF, dict]] = []

    def set_data(self, days: list[dict]):
        self.days = days
        self.update()

    def paintEvent(self, _):
        pa = QPainter(self)
        pa.setRenderHint(QPainter.Antialiasing)
        col = _colors()
        f = QFont(self.font())
        f.setPointSizeF(max(7.5, f.pointSizeF() - 1))
        pa.setFont(f)
        fm = pa.fontMetrics()
        left, right, top, bottom = 34, 8, 8, 22
        w, h = self.width() - left - right, self.height() - top - bottom
        top_val = max([d["ok"] + d["warn"] + d["fail"] for d in self.days] + [1])
        step = max(1, -(-top_val // 4))
        top_val = step * 4
        # сетка и подписи оси — приглушённые
        grid = QColor(C["border"])
        for i in range(5):
            y = top + h - h * i / 4
            pa.setPen(QPen(grid, 1))
            pa.drawLine(left, int(y), left + w, int(y))
            pa.setPen(QColor(C["faint"]))
            pa.drawText(QRectF(0, y - 8, left - 6, 16), Qt.AlignRight | Qt.AlignVCenter, str(step * i))
        self._bars = []
        n = max(1, len(self.days))
        slot = w / n
        bw = max(3.0, min(18.0, slot * 0.6))
        label_every = max(1, int(fm.horizontalAdvance("00.00") * 1.6 // slot) + 1)
        for i, d in enumerate(self.days):
            x = left + slot * i + (slot - bw) / 2
            y = top + h
            segs = [(k, d[k]) for k in ("ok", "warn", "fail") if d[k]]
            for j, (k, v) in enumerate(segs):
                sh = h * v / top_val
                gap = 2 if j < len(segs) - 1 else 0  # 2px просвет между сегментами
                r = QRectF(x, y - sh + gap, bw, max(1.0, sh - gap))
                path = QPainterPath()
                if j == len(segs) - 1:  # скругляем только верхний конец столбца
                    rad = min(4.0, bw / 2, r.height())
                    path.moveTo(r.left(), r.bottom())
                    path.lineTo(r.left(), r.top() + rad)
                    path.quadTo(r.left(), r.top(), r.left() + rad, r.top())
                    path.lineTo(r.right() - rad, r.top())
                    path.quadTo(r.right(), r.top(), r.right(), r.top() + rad)
                    path.lineTo(r.right(), r.bottom())
                    path.closeSubpath()
                else:
                    path.addRect(r)
                pa.fillPath(path, col[k])
                y -= sh
            self._bars.append((QRectF(left + slot * i, top, slot, h), d))
            if (len(self.days) - 1 - i) % label_every == 0:
                pa.setPen(QColor(C["faint"]))
                pa.drawText(QRectF(left + slot * i - 30 + slot / 2, top + h + 4, 60, 16), Qt.AlignCenter,
                            d["date"].strftime("%d.%m"))
        if not any(d["runs"] for d in self.days):
            pa.setPen(QColor(C["muted"]))
            pa.drawText(QRectF(left, top, w, h), Qt.AlignCenter, "За этот период проверок не было")
        pa.end()

    def mouseMoveEvent(self, e):
        for r, d in self._bars:
            if r.contains(e.position()):
                if d["runs"]:
                    QToolTip.showText(e.globalPosition().toPoint(),
                                      f"{d['date'].strftime('%d.%m.%Y')}\n✓ {d['ok']}   ! {d['warn']}   ✗ {d['fail']}\n"
                                      f"профилей: {d['profiles']}, проверок: {d['runs']}", self)
                else:
                    QToolTip.showText(e.globalPosition().toPoint(), f"{d['date'].strftime('%d.%m.%Y')}: проверок не было",
                                      self)
                return
        QToolTip.hideText()


class HealthView(QWidget):
    def __init__(self, pids: list[int]):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        row = QHBoxLayout()
        self.scope = QComboBox()
        if pids:
            names = ", ".join(f"#{p}" for p in pids[:5]) + ("…" if len(pids) > 5 else "")
            self.scope.addItem(f"Выбранные профили ({names})", pids)
        self.scope.addItem("Все профили", None)
        self.kind = QComboBox()
        self.kind.addItem("Проверка и детекторы", None)
        for k, t in health.KINDS.items():
            self.kind.addItem(t, k)
        self.period = QComboBox()
        for d in (7, 30, 90):
            self.period.addItem(f"{d} дней", d)
        self.period.setCurrentIndex(1)
        for cb in (self.scope, self.kind, self.period):
            cb.currentIndexChanged.connect(lambda _: self.reload())
            row.addWidget(cb)
        row.addStretch()
        v.addLayout(row)
        legend = QHBoxLayout()
        legend.setSpacing(14)
        for k, label in _STATES:
            sw = QLabel()
            sw.setFixedSize(10, 10)
            sw.setStyleSheet(f"background:{_colors()[k].name()}; border-radius:2px;")
            t = QLabel(label)
            t.setObjectName("hint")
            legend.addWidget(sw)
            legend.addWidget(t)
        legend.addStretch()
        v.addLayout(legend)
        self.chart = HealthChart()
        v.addWidget(self.chart)
        cap = QLabel("Столбец — сумма пунктов последней проверки дня каждого профиля. Ниже — последние проверки; "
                     "«хуже» — пункт стал ! или ✗ по сравнению с прошлой проверкой того же вида.")
        cap.setObjectName("hint")
        cap.setWordWrap(True)
        v.addWidget(cap)
        self.list = QListWidget()
        self.list.setObjectName("boxList")
        self.list.setMinimumHeight(170)
        v.addWidget(self.list)
        self.reload()

    def reload(self):
        pids, kind, days = self.scope.currentData(), self.kind.currentData(), self.period.currentData()
        self.chart.set_data(health.daily(pids, kind, days))
        names = {p.id: p.name for p in db.list_profiles()}
        self.list.clear()
        since = (dt.datetime.now() - dt.timedelta(days=days)).timestamp()
        for r in health.recent(pids, kind, limit=80):
            if r["ts"] < since:
                break
            when = dt.datetime.fromtimestamp(r["ts"]).strftime("%d.%m %H:%M")
            text = (f"{when}   #{r['profile_id']} {names.get(r['profile_id'], '')}   {health.KINDS[r['kind']]}:   "
                    f"✓ {r['ok']}   ! {r['warn']}   ✗ {r['fail']}")
            if r["worse"]:
                text += "   ▼ хуже: " + ", ".join(t for _, t, *_ in r["worse"][:3]) + ("…" if len(r["worse"]) > 3 else "")
            elif r["better"]:
                text += "   ▲ лучше"
            it = QListWidgetItem(text)
            it.setToolTip("\n".join(f"{ {'ok': '✓', 'warn': '!', 'fail': '✗'}[s]} {t}" + (f" — {d}" if d else "")
                                    for s, t, d in r["items"]))
            if r["worse"]:
                it.setForeground(QColor(C["red"]))
            self.list.addItem(it)
        if not self.list.count():
            self.list.addItem("Проверок пока нет — «Проверить отпечаток» или задача «Здоровье» в Планировщике")


def health_modal(host, pids: list[int]):
    Modal(host, "Здоровье профилей", HealthView(pids), ok_text="Закрыть", cancel_text=None, width=820).exec()
