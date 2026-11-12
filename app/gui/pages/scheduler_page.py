"""Планировщик: задачи по расписанию (прогрев, сценарии, проверки, резервные копии)."""
import datetime as dt
import time

from PySide6.QtCore import QDateTime, Qt, QTime
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateTimeEdit, QFormLayout, QFrame,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget)

from ... import db
from ...manager import manager
from ...scheduler import ACTIONS, WEEKDAYS, describe_schedule, describe_targets, next_run
from ..icons import icon
from ..modal import Modal, confirm
from ..theme import C
from ..widgets import empty_label, set_icon, side_title, tool
from ..workers import run_bg

COLS = ["", "Название", "Действие", "Профили", "Расписание", "Следующий запуск", "Последний результат"]
WIDTHS = (34, 210, 200, 120, 180, 140, 0)  # 0 = растягивается


def _when(ts) -> str:
    if not ts:
        return "—"
    d = dt.datetime.fromtimestamp(ts)
    today = dt.date.today()
    day = "сегодня" if d.date() == today else "завтра" if d.date() == today + dt.timedelta(days=1) else d.strftime("%d.%m")
    return f"{day} {d.strftime('%H:%M')}"


class TaskForm(QWidget):
    def __init__(self, task: dict):
        super().__init__()
        self.task = task
        f = QFormLayout(self)
        f.setContentsMargins(0, 0, 0, 0)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(10)

        def row(label, w):
            lbl = QLabel(label)
            lbl.setObjectName("formLabel")
            f.addRow(lbl, w)
            return lbl

        self.name = QLineEdit(task.get("name", ""))
        self.name.setPlaceholderText("Например: утренний прогрев")
        row("Название", self.name)
        self.action = QComboBox()
        for k, v in ACTIONS.items():
            self.action.addItem(v, k)
        self.action.setCurrentIndex(max(0, self.action.findData(task.get("action", "warmup"))))
        row("Действие", self.action)
        self.scenario = QComboBox()
        for d in db.list_docs("scenarios"):
            self.scenario.addItem(d.get("name") or f"Сценарий {d['id']}", d["id"])
        self.scenario.setCurrentIndex(max(0, self.scenario.findData(task.get("scenario_id"))))
        self.scenario_lbl = row("Сценарий", self.scenario)
        self.headless = QCheckBox("Без окна (в фоне)")
        self.headless.setChecked(bool(task.get("headless", True)))
        f.addRow("", self.headless)

        tg = task.get("targets") or {"mode": "ids", "ids": []}
        self.mode = QComboBox()
        for label, k in (("Отмеченные профили", "ids"), ("Профили из папки", "folder"), ("Профили с тегом", "tag"),
                         ("Все профили", "all")):
            self.mode.addItem(label, k)
        self.mode.setCurrentIndex(max(0, self.mode.findData(tg.get("mode", "ids"))))
        self.mode_lbl = row("Профили", self.mode)
        profiles = db.list_profiles()
        self.value = QComboBox()
        self._folders = sorted({p.folder for p in profiles if p.folder})
        self._tags = sorted({t for p in profiles for t in p.tags})
        self.ids = QListWidget()
        self.ids.setObjectName("boxList")
        self.ids.setFixedHeight(130)
        for p in profiles:
            it = QListWidgetItem(f"#{p.id}  {p.name}")
            it.setData(Qt.UserRole, p.id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if p.id in tg.get("ids", []) else Qt.Unchecked)
            self.ids.addItem(it)
        f.addRow("", self.value)
        f.addRow("", self.ids)
        self._value0 = tg.get("value", "")

        sch = task.get("schedule") or {"kind": "daily", "time": "09:00"}
        self.kind = QComboBox()
        for label, k in (("По дням недели в заданное время", "daily"), ("Через интервал", "interval"),
                         ("Один раз", "once")):
            self.kind.addItem(label, k)
        self.kind.setCurrentIndex(max(0, self.kind.findData(sch.get("kind", "daily"))))
        row("Расписание", self.kind)
        self.time = QTimeEdit(QTime.fromString(sch.get("time", "09:00"), "HH:mm"))
        self.time.setDisplayFormat("HH:mm")
        self.time_lbl = row("Время", self.time)
        self.days_box = QWidget()
        dl = QHBoxLayout(self.days_box)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(8)
        days = set(sch.get("days") or range(7))
        self.days = []
        for i, name in enumerate(WEEKDAYS):
            c = QCheckBox(name)
            c.setChecked(i in days)
            self.days.append(c)
            dl.addWidget(c)
        dl.addStretch()
        self.days_lbl = row("Дни", self.days_box)
        self.every = QSpinBox()
        self.every.setRange(1, 7 * 24 * 60)
        self.every.setSuffix(" мин")
        self.every.setValue(int(sch.get("every", 60)))
        self.every_lbl = row("Каждые", self.every)
        self.at = QDateTimeEdit(QDateTime.fromString(sch.get("at") or "", "yyyy-MM-dd HH:mm")
                                if sch.get("at") else QDateTime.currentDateTime().addSecs(3600))
        self.at.setDisplayFormat("dd.MM.yyyy HH:mm")
        self.at.setCalendarPopup(True)
        self.at_lbl = row("Когда", self.at)
        self.preview = QLabel()
        self.preview.setObjectName("hint")
        self.preview.setWordWrap(True)
        f.addRow("", self.preview)

        for w in (self.action, self.mode, self.kind):
            w.currentIndexChanged.connect(lambda _: self._sync())
        self.at.dateTimeChanged.connect(lambda _: self._sync())
        self.time.timeChanged.connect(lambda _: self._sync())
        self.every.valueChanged.connect(lambda _: self._sync())
        for c in self.days:
            c.toggled.connect(lambda _: self._sync())
        self._sync()

    def _sync(self):
        action, mode, kind = self.action.currentData(), self.mode.currentData(), self.kind.currentData()
        self.scenario.setVisible(action == "scenario")
        self.scenario_lbl.setVisible(action == "scenario")
        self.headless.setVisible(action in ("scenario", "start"))
        targets = action not in ("backup", "engine_check")
        self.mode.setVisible(targets)
        self.mode_lbl.setVisible(targets)
        self.ids.setVisible(targets and mode == "ids")
        self.value.setVisible(targets and mode in ("folder", "tag"))
        if mode in ("folder", "tag"):
            items = self._folders if mode == "folder" else self._tags
            cur = self.value.currentText() or self._value0
            self.value.blockSignals(True)
            self.value.clear()
            self.value.addItems(items or ["— нет —"])
            if cur in items:
                self.value.setCurrentText(cur)
            self.value.blockSignals(False)
        for w, on in ((self.time, kind == "daily"), (self.time_lbl, kind == "daily"), (self.days_box, kind == "daily"),
                      (self.days_lbl, kind == "daily"), (self.every, kind == "interval"),
                      (self.every_lbl, kind == "interval"), (self.at, kind == "once"), (self.at_lbl, kind == "once")):
            w.setVisible(on)
        t = self.result()
        nr = next_run(t, time.time())
        self.preview.setText(f"{describe_schedule(t)} · ближайший запуск: {_when(nr) if nr else 'не будет'}. "
                             "Задачи выполняются, пока открыт claudeAnty.")
        m = self.parent()  # высота формы поменялась — модальное окно пересчитывает размер карточки
        while m is not None and not hasattr(m, "_place"):
            m = m.parent()
        if m is not None and m.isVisible():
            m._place()

    def result(self) -> dict:
        t = dict(self.task)
        t["name"] = self.name.text().strip() or ACTIONS[self.action.currentData()]
        t["action"] = self.action.currentData()
        t["scenario_id"] = self.scenario.currentData()
        t["headless"] = self.headless.isChecked()
        mode = self.mode.currentData()
        t["targets"] = {"mode": mode, "ids": [self.ids.item(i).data(Qt.UserRole) for i in range(self.ids.count())
                                              if self.ids.item(i).checkState() == Qt.Checked],
                        "value": self.value.currentText() if mode in ("folder", "tag") else ""}
        kind = self.kind.currentData()
        t["schedule"] = {"kind": kind, "time": self.time.time().toString("HH:mm"),
                         "days": [i for i, c in enumerate(self.days) if c.isChecked()],
                         "every": self.every.value(), "at": self.at.dateTime().toString("yyyy-MM-dd HH:mm")}
        return t

    def validate(self):
        t = self.result()
        if t["action"] == "scenario" and not t.get("scenario_id"):
            return "Сначала создайте сценарий на вкладке «Сценарии»"
        if t["action"] not in ("backup", "engine_check"):
            m = t["targets"]["mode"]
            if m == "ids" and not t["targets"]["ids"]:
                return "Отметьте хотя бы один профиль"
            if m in ("folder", "tag") and t["targets"]["value"] in ("", "— нет —"):
                return "Нет папок или тегов — выберите другой способ"
        if t["schedule"]["kind"] == "daily" and not t["schedule"]["days"]:
            return "Отметьте хотя бы один день недели"
        if t["schedule"]["kind"] == "once" and not next_run({**t, "last_run": None}, time.time()):
            return "Время однократного запуска уже прошло"
        return None


class SchedulerSidebar(QFrame):
    def __init__(self, page):
        super().__init__()
        self.setObjectName("sideBar")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(side_title("ПЛАНИРОВЩИК", tool("plus", "Новая задача", page.new_task)))
        hint = QLabel(
            "Задачи выполняются автоматически, пока открыт claudeAnty: прогрев, сценарии, проверки отпечатков, "
            "резервные копии.\n\nЕсли приложение было закрыто в момент запуска, задача выполнится при открытии, "
            "но только если опоздание не больше 30 минут.\n\nПрофили выбираются списком, по папке или тегу — "
            "новые профили в папке подхватываются сами.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        hint.setContentsMargins(18, 4, 14, 0)
        hint.setAlignment(Qt.AlignTop)
        v.addWidget(hint)
        v.addStretch()


class SchedulerPage(QWidget):
    key, title, icon_name = "scheduler", "Планировщик", "clock"
    search_hint = "Поиск задач"

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.sidebar = SchedulerSidebar(self)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 10, 16, 10)
        hl.setSpacing(6)
        self.crumbs = QLabel()
        self.crumbs.setObjectName("crumbs")
        hl.addWidget(self.crumbs)
        hl.addStretch()
        run = QPushButton("  Выполнить сейчас")
        set_icon(run, "play", "text", 12)
        run.clicked.connect(self.run_now)
        add = QPushButton("  Новая задача")
        add.setObjectName("primary")
        add.setIcon(icon("plus", "#ffffff", 12))
        add.clicked.connect(self.new_task)
        for b in (run, add):
            b.setCursor(Qt.PointingHandCursor)
            hl.addWidget(b)
        v.addWidget(head)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels([c.upper() for c in COLS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        hh = self.table.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for i, w in enumerate(WIDTHS):
            if w:
                self.table.setColumnWidth(i, w)
            else:
                hh.setSectionResizeMode(i, QHeaderView.Stretch)
        self.table.doubleClicked.connect(lambda _: self.edit_task())
        self.table.itemChanged.connect(self._toggled)
        v.addWidget(self.table, 1)
        self.empty = empty_label("Задач пока нет.\nНапример: «прогреть профили из папки каждый день в 9:00».")
        v.addWidget(self.empty, 1)

        self.tab_actions = QWidget()
        al = QHBoxLayout(self.tab_actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(2)
        al.addStretch()
        al.addWidget(tool("play", "Выполнить сейчас", self.run_now, 14))
        al.addWidget(tool("edit", "Изменить задачу", self.edit_task))
        al.addWidget(tool("trash", "Удалить задачу", self.delete_task))

    def refresh(self):
        q = self.win.search.text().lower().strip()
        tasks = [t for t in db.list_docs("tasks") if not q or q in t.get("name", "").lower()]
        sel = self._current_id()
        self.table.blockSignals(True)
        self.table.setRowCount(len(tasks))
        for r, t in enumerate(tasks):
            on = t.get("enabled", True)
            running = t["id"] in manager.scheduler.running
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Checked if on else Qt.Unchecked)
            chk.setData(Qt.UserRole, t["id"])
            chk.setToolTip("Включена" if on else "Выключена")
            self.table.setItem(r, 0, chk)
            action = ACTIONS.get(t.get("action"), t.get("action"))
            if t.get("action") == "scenario":
                sc = db.get_doc("scenarios", t.get("scenario_id") or 0)
                action = f"Сценарий «{sc['name']}»" if sc else "Сценарий (удалён!)"
            vals = [t.get("name", ""), action, describe_targets(t), describe_schedule(t),
                    "выполняется…" if running else (_when(t.get("next_run") or next_run(t, time.time()))
                                                    if on else "выключена"),
                    (f"{_when(t.get('last_run'))}: {t.get('last_result', '')}" if t.get("last_run") else "—")]
            for c, val in enumerate(vals, 1):
                it = QTableWidgetItem(val)
                if c == 5 and running:
                    it.setForeground(QColor(C["accent"]))
                elif c == 6 and "ошибка" in val:
                    it.setForeground(QColor(C["red"]))
                elif c in (3, 4, 6) or not on:
                    it.setForeground(QColor(C["muted"] if on else C["faint"]))
                if c == 6:
                    it.setToolTip(val)
                self.table.setItem(r, c, it)
            if t["id"] == sel:
                self.table.selectRow(r)
        self.table.blockSignals(False)
        self.table.setVisible(bool(tasks))
        self.empty.setVisible(not tasks)
        n_on = sum(1 for t in tasks if t.get("enabled", True))
        self.crumbs.setText(f"Планировщик  ·  задач {len(tasks)}, включено {n_on}")

    def _current_id(self):
        r = self.table.currentRow()
        it = self.table.item(r, 0) if r >= 0 else None
        return it.data(Qt.UserRole) if it else None

    def _toggled(self, item: QTableWidgetItem):
        if item.column() != 0:
            return
        t = db.get_doc("tasks", item.data(Qt.UserRole))
        if not t:
            return
        t["enabled"] = item.checkState() == Qt.Checked
        t["next_run"] = next_run(t, time.time()) if t["enabled"] else None
        db.save_doc("tasks", t)
        self.win.log(f"Задача «{t['name']}» " + ("включена" if t["enabled"] else "выключена"))
        self.refresh()

    def _edit(self, task: dict, title: str, ok: str):
        form = TaskForm(task)
        if not Modal(self.win, title, form, ok_text=ok, width=620, validate=form.validate).exec():
            return
        t = form.result()
        t.setdefault("enabled", True)
        t["next_run"] = next_run(t, time.time()) if t["enabled"] else None
        t = db.save_doc("tasks", t)
        self.win.log(f"Задача «{t['name']}» сохранена: {describe_schedule(t)}, ближайший запуск {_when(t['next_run'])}")
        self.refresh()

    def new_task(self):
        pids = self.win.pages["profiles"].selected_ids()
        self._edit({"action": "warmup", "headless": True, "targets": {"mode": "ids", "ids": pids},
                    "schedule": {"kind": "daily", "time": "09:00", "days": list(range(7))}}, "Новая задача", "Создать")

    def edit_task(self):
        tid = self._current_id()
        t = db.get_doc("tasks", tid) if tid else None
        if t:
            self._edit(t, "Изменить задачу", "Сохранить")

    def delete_task(self):
        tid = self._current_id()
        t = db.get_doc("tasks", tid) if tid else None
        if t and confirm(self.win, "Удалить задачу?", f"«{t['name']}» больше не будет выполняться."):
            db.delete_doc("tasks", tid)
            self.refresh()

    def run_now(self):
        tid = self._current_id()
        if not tid:
            self.win.log("Выберите задачу в таблице")
            return
        if tid in manager.scheduler.running:
            self.win.log("Задача уже выполняется")
            return
        run_bg(lambda: manager.run(manager.scheduler.run_task(tid, manual=True), timeout=None),
               lambda r, e: (self.win.log(f"Ошибка задачи: {e}") if e else None, self.refresh()), self)
        self.refresh()
