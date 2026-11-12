"""Конструктор сценариев (RPA) без кода: список шагов, редактор шага, запуск на нескольких профилях."""
import copy

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QSpinBox, QVBoxLayout,
                               QWidget)

from ... import db, rpa
from ...manager import manager
from ..icons import icon
from ..modal import Modal, confirm
from ..theme import C
from ..widgets import empty_label, fill_list, set_icon, side_list, side_title, tool
from ..workers import run_bg

_GROUPS = [("Навигация", ["open", "new_tab"]), ("Действия", ["click", "type", "press", "scroll", "captcha"]),
           ("Ожидание", ["wait", "wait_for"]), ("Данные", ["extract", "set"]),
           ("Коды подтверждения", ["wait_email", "get_number", "wait_sms"]), ("Блоки", ["repeat", "if", "end"]),
           ("Прочее", ["screenshot"])]
_STEP_ICONS = {"open": "globe", "new_tab": "plus", "click": "check", "type": "edit", "press": "key", "wait": "clock",
               "wait_for": "search", "scroll": "arrow_down", "repeat": "repeat", "if": "layers", "end": "chevron_up",
               "screenshot": "download", "extract": "copy", "set": "tag", "captcha": "shield",
               "wait_email": "mail", "get_number": "phone", "wait_sms": "phone"}
VARS_HINT = ("Подстановки: {{login}}, {{password}}, {{totp}} — из «Пароли и 2FA» профиля "
             "({{password:Google}} — аккаунт Google), {{name}}, {{id}}, {{random:6}}, {{date}}, а также "
             "{{колонка}} из «Данные (CSV)», переменные из шагов «Извлечь» / «Задать», {{email_code}} — код из "
             "письма, {{phone}} / {{phone_digits}} и {{sms_code}} — номер и код из SMS.")


def step_form(step: dict) -> tuple[QWidget, callable]:
    """Форма полей шага по описанию rpa.STEP_TYPES. Возвращает (виджет, функция-результат)."""
    w = QWidget()
    f = QFormLayout(w)
    f.setContentsMargins(0, 0, 0, 0)
    f.setHorizontalSpacing(16)
    f.setVerticalSpacing(10)
    getters = {}
    for key, label, kind, default in rpa.STEP_TYPES[step["type"]][1]:
        val = step.get(key, default)
        if kind == "bool":
            ed = QCheckBox(label)
            ed.setChecked(bool(val))
            getters[key] = ed.isChecked
            f.addRow("", ed)
            continue
        if kind == "int":
            ed = QSpinBox()
            ed.setRange(0, 100000)
            ed.setValue(int(val))
            getters[key] = ed.value
        elif kind == "float":
            ed = QDoubleSpinBox()
            ed.setRange(0, 3600)
            ed.setDecimals(1)
            ed.setSingleStep(0.5)
            ed.setValue(float(val))
            getters[key] = ed.value
        elif kind.startswith("choice:"):
            ed = QComboBox()
            for opt in kind[7:].split(","):
                ed.addItem(rpa.CHOICE_LABELS.get(opt, opt), opt)
            ed.setCurrentIndex(max(0, ed.findData(val)))
            getters[key] = ed.currentData
        else:
            ed = QLineEdit(str(val))
            ed.setMinimumWidth(360)
            if key == "selector":
                ed.setPlaceholderText("#login, .btn-primary, text=Войти, xpath=//button")
            elif key == "key":
                ed.setPlaceholderText("Enter, Tab, Escape, Control+A, ArrowDown…")
            getters[key] = ed.text
        lbl = QLabel(label)
        lbl.setObjectName("formLabel")
        f.addRow(lbl, ed)
    if any(k == "selector" for k, *_ in rpa.STEP_TYPES[step["type"]][1]):
        h = QLabel(rpa.SELECTOR_HINT)
        h.setObjectName("hint")
        h.setWordWrap(True)
        f.addRow("", h)
    if step["type"] == "wait_email":
        h = QLabel("Ящик — аккаунт в «Пароли и 2FA» профиля: логин — адрес почты, пароль — пароль приложения "
                   "(в Gmail, Яндексе, Mail.ru, Outlook обычный пароль по IMAP не работает), IMAP должен быть "
                   "включён в настройках почты. Ищутся письма, пришедшие после начала сценария. Регулярное "
                   "выражение с группой берёт группу: «code: (\\w+)», ссылку — «(https://\\S+confirm\\S+)».")
        h.setObjectName("hint")
        h.setWordWrap(True)
        f.addRow("", h)
    if step["type"] in ("get_number", "wait_sms"):
        h = QLabel("Сервис и ключ — Настройки → Сценарии. «Получить номер» кладёт номер в {{phone}} (с +) и "
                   "{{phone_digits}}; «Ждать SMS» — код в {{sms_code}} и завершает активацию. Не пришёл код или "
                   "сценарий упал — номер отменяется, деньги возвращаются.")
        h.setObjectName("hint")
        h.setWordWrap(True)
        f.addRow("", h)
    if step["type"] in ("type", "open", "new_tab"):
        h = QLabel(VARS_HINT)
        h.setObjectName("hint")
        h.setWordWrap(True)
        f.addRow("", h)
    if not getters:
        h = QLabel("У этого шага нет настроек.")
        h.setObjectName("hint")
        f.addRow("", h)
    return w, lambda: {"type": step["type"], **{k: g() for k, g in getters.items()}}


class ScenariosSidebar(QFrame):
    def __init__(self, page: "ScenariosPage"):
        super().__init__()
        self.setObjectName("sideBar")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(side_title("СЦЕНАРИИ", tool("plus", "Новый сценарий", page.new_scenario),
                               tool("copy", "Дублировать сценарий", page.duplicate_scenario),
                               tool("trash", "Удалить сценарий", page.delete_scenario)))
        self.list = side_list()
        self.list.itemClicked.connect(lambda it: page.select(it.data(Qt.UserRole)))
        v.addWidget(self.list, 1)


class ScenariosPage(QWidget):
    key, title, icon_name = "scenarios", "Сценарии", "workflow"
    search_hint = "Поиск сценариев"

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.sidebar = ScenariosSidebar(self)
        self.current: dict | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 16, 24, 14)
        v.setSpacing(10)

        self.editor = QWidget()
        ev = QVBoxLayout(self.editor)
        ev.setContentsMargins(0, 0, 0, 0)
        ev.setSpacing(8)
        top = QHBoxLayout()
        lbl = QLabel("Название")
        lbl.setObjectName("formLabel")
        self.name = QLineEdit()
        self.name.editingFinished.connect(self._rename)
        top.addWidget(lbl)
        top.addWidget(self.name, 1)
        ev.addLayout(top)

        self.steps = QListWidget()
        self.steps.setObjectName("boxList")
        self.steps.setIconSize(QSize(14, 14))
        self.steps.itemDoubleClicked.connect(lambda _: self.edit_step())
        ev.addWidget(self.steps, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.add_btn = QPushButton("  Добавить шаг")
        set_icon(self.add_btn, "plus", "text", 14)
        menu = QMenu(self.add_btn)
        for group, types in _GROUPS:
            sec = menu.addSection(group)
            sec.setEnabled(False)
            for t in types:
                a = QAction(icon(_STEP_ICONS[t], C["text"], 14), rpa.STEP_TYPES[t][0], menu)
                a.triggered.connect(lambda _=False, t=t: self.add_step(t))
                menu.addAction(a)
        self.add_btn.setMenu(menu)
        row.addWidget(self.add_btn)
        self.rec_btn = QPushButton("  Записать")
        self.rec_btn.setIcon(icon("play", C["red"], 12))
        self.rec_btn.setToolTip("Записать шаги действиями в профиле: клики, ввод, прокрутка, переходы")
        self.rec_btn.clicked.connect(self.toggle_record)
        row.addWidget(self.rec_btn)
        for text, ico, fn in (("  Изменить", "edit", self.edit_step), ("", "copy", self.duplicate_step),
                              ("", "arrow_up", lambda: self.move_step(-1)), ("", "arrow_down", lambda: self.move_step(1)),
                              ("", "trash", self.delete_step)):
            b = QPushButton(text)
            set_icon(b, ico, "text", 14)
            b.setToolTip({"copy": "Дублировать шаг", "arrow_up": "Выше", "arrow_down": "Ниже", "trash": "Удалить шаг",
                          "edit": "Изменить шаг"}[ico])
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        self.data_btn = QPushButton("  Данные (CSV)")
        set_icon(self.data_btn, "layers", "text", 14)
        self.data_btn.setToolTip("Таблица данных: строка на профиль, колонки — переменные {{колонка}}")
        self.data_btn.clicked.connect(self.edit_data)
        row.addWidget(self.data_btn)
        ev.addLayout(row)
        self.problem = QLabel()
        self.problem.setObjectName("modalError")
        self.problem.setWordWrap(True)
        ev.addWidget(self.problem)
        hint = QLabel(VARS_HINT + " Блоки «Повторить» и «Если» действуют до своего «Конец блока».")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        ev.addWidget(hint)

        run_row = QHBoxLayout()
        self.jobs_label = QLabel()
        self.jobs_label.setObjectName("hint")
        self.stop_btn = QPushButton("  Остановить все")
        set_icon(self.stop_btn, "stop", "text", 12)
        self.stop_btn.clicked.connect(self.stop_all)
        self.run_btn = QPushButton("  Запустить на профилях…")
        self.run_btn.setObjectName("primary")
        self.run_btn.setIcon(icon("play", "#ffffff", 12))
        self.run_btn.clicked.connect(self.run_dialog)
        run_row.addWidget(self.jobs_label, 1)
        run_row.addWidget(self.stop_btn)
        run_row.addWidget(self.run_btn)
        ev.addLayout(run_row)
        for b in self.editor.findChildren(QPushButton):
            b.setCursor(Qt.PointingHandCursor)
        v.addWidget(self.editor, 1)
        self.empty = empty_label("Сценариев пока нет.\nНажмите «+» слева, чтобы создать первый.")
        v.addWidget(self.empty, 1)

    # ---------- список сценариев ----------
    def refresh(self):
        q = self.win.search.text().lower().strip()
        docs = [d for d in db.list_docs("scenarios") if not q or q in d.get("name", "").lower()]
        cur = self.current["id"] if self.current else None
        if cur is not None and not any(d["id"] == cur for d in docs):
            cur = None
        if cur is None and docs:
            cur = docs[0]["id"]
        fill_list(self.sidebar.list, [(f"{d.get('name') or 'Без названия'}   {len(d.get('steps', []))}", d["id"], "workflow")
                                      for d in docs], cur)
        if cur is not None and (not self.current or self.current["id"] != cur):
            self.select(cur)
        elif cur is None:
            self.current = None
        self.editor.setVisible(self.current is not None)
        self.empty.setVisible(self.current is None)
        rec = manager.recorder.status()
        self.rec_btn.setText(f"  Остановить запись ({rec['steps']})" if rec["active"] else "  Записать")
        self.rec_btn.setIcon(icon("stop" if rec["active"] else "play", C["red"], 12))
        rows = len(rpa.parse_data(self.current.get("data", ""))) if self.current else 0
        self.data_btn.setText(f"  Данные (CSV): {rows}" if rows else "  Данные (CSV)")
        jobs = list(rpa.jobs.running.values())
        self.jobs_label.setText(" · ".join(f"«{j['name']}»: {j['done']}/{j['total']}" for j in jobs)
                                if jobs else "Сейчас ничего не выполняется")
        self.stop_btn.setEnabled(bool(jobs))

    def select(self, sid: int):
        self.current = db.get_doc("scenarios", sid)
        if self.current:
            self.name.setText(self.current.get("name", ""))
            self._fill_steps()

    def _fill_steps(self, select: int | None = None):
        steps = self.current.get("steps", [])
        row = self.steps.currentRow() if select is None else select
        self.steps.clear()
        depth = 0
        for i, s in enumerate(steps):
            if s["type"] == "end":
                depth = max(0, depth - 1)
            it = QListWidgetItem(icon(_STEP_ICONS.get(s["type"], "check"), C["muted"], 14),
                                 f"{i + 1:>2}.  " + "      " * depth + rpa.describe(s))
            if s["type"] in rpa.BLOCKS or s["type"] == "end":
                it.setForeground(QColor(C["accent"]))
            self.steps.addItem(it)
            if s["type"] in rpa.BLOCKS:
                depth += 1
        if steps:
            self.steps.setCurrentRow(max(0, min(row, len(steps) - 1)))
        err = rpa.validate(steps) if steps else None
        self.problem.setText("⚠ " + err if err else "")
        self.problem.setVisible(bool(err))

    def _save(self, select: int | None = None):
        db.save_doc("scenarios", self.current)
        self._fill_steps(select)
        self.refresh()

    def _rename(self):
        if self.current and self.name.text().strip() != self.current.get("name"):
            self.current["name"] = self.name.text().strip() or "Без названия"
            self._save()

    def new_scenario(self):
        self.current = db.save_doc("scenarios", {"name": "Новый сценарий", "steps": [
            {"type": "open", "url": "https://www.google.com"}, {"type": "wait", "min": 2.0, "max": 4.0},
            {"type": "scroll", "direction": "down", "amount": 800, "times": 2}]})
        self.name.setText(self.current["name"])
        self._fill_steps(0)
        self.refresh()
        self.name.setFocus()
        self.name.selectAll()

    def duplicate_scenario(self):
        if self.current:
            d = copy.deepcopy(self.current)
            d.pop("id", None)
            d["name"] = d.get("name", "") + " (копия)"
            self.current = db.save_doc("scenarios", d)
            self.select(self.current["id"])
            self.refresh()

    def delete_scenario(self):
        if self.current and confirm(self.win, "Удалить сценарий?", f"«{self.current.get('name')}» будет удалён. "
                                    "Задачи планировщика с этим сценарием перестанут работать."):
            db.delete_doc("scenarios", self.current["id"])
            self.current = None
            self.refresh()

    # ---------- шаги ----------
    def add_step(self, t: str):
        if not self.current:
            return
        step = rpa.default_step(t)
        if t != "end" and rpa.STEP_TYPES[t][1]:
            w, get = step_form(step)
            if not Modal(self.win, rpa.STEP_TYPES[t][0], w, ok_text="Добавить", width=560).exec():
                return
            step = get()
        steps = self.current.setdefault("steps", [])
        pos = self.steps.currentRow() + 1 if self.steps.currentRow() >= 0 else len(steps)
        steps.insert(pos, step)
        if t in rpa.BLOCKS:
            steps.insert(pos + 1, {"type": "end"})  # блок сразу с концом — шаги добавляются внутрь
        self._save(pos)

    def edit_step(self):
        r = self.steps.currentRow()
        if not self.current or r < 0:
            return
        step = self.current["steps"][r]
        if not rpa.STEP_TYPES[step["type"]][1]:
            return
        w, get = step_form(step)
        if Modal(self.win, rpa.STEP_TYPES[step["type"]][0], w, ok_text="Сохранить", width=560).exec():
            self.current["steps"][r] = get()
            self._save(r)

    def duplicate_step(self):
        r = self.steps.currentRow()
        if self.current and r >= 0:
            self.current["steps"].insert(r + 1, copy.deepcopy(self.current["steps"][r]))
            self._save(r + 1)

    def move_step(self, d: int):
        r = self.steps.currentRow()
        steps = self.current.get("steps", []) if self.current else []
        if 0 <= r < len(steps) and 0 <= r + d < len(steps):
            steps[r], steps[r + d] = steps[r + d], steps[r]
            self._save(r + d)

    def delete_step(self):
        r = self.steps.currentRow()
        if self.current and r >= 0:
            del self.current["steps"][r]
            self._save(r)

    # ---------- запуск ----------
    def run_dialog(self, pids: list[int] | None = None):
        if not self.current:
            return
        err = rpa.validate(self.current.get("steps", []))
        if err:
            self.win.log(f"Сценарий не запущен: {err}")
            return
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lst = QListWidget()
        lst.setObjectName("boxList")
        lst.setFixedHeight(240)
        pre = set(pids or self.win.pages["profiles"].selected_ids())
        for p in db.list_profiles():
            it = QListWidgetItem(f"{'●' if manager.is_running(p.id) else '○'}  #{p.id}  {p.name}"
                                 + (f"   · {p.folder}" if p.folder else ""))
            it.setData(Qt.UserRole, p.id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if p.id in pre else Qt.Unchecked)
            lst.addItem(it)
        all_row = QHBoxLayout()
        for text, on in (("Отметить все", True), ("Снять все", False)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, on=on: [lst.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)
                                                      for i in range(lst.count())])
            all_row.addWidget(b)
        all_row.addStretch()
        headless = QCheckBox("Без окна (в фоне)")
        close_after = QCheckBox("Закрыть профиль после сценария (если он не был запущен)")
        close_after.setChecked(True)
        lay.addWidget(QLabel(f"Сценарий «{self.current.get('name')}», шагов: {len(self.current.get('steps', []))}"))
        lay.addWidget(lst)
        lay.addLayout(all_row)
        lay.addWidget(headless)
        lay.addWidget(close_after)
        h = QLabel("Одновременно выполняется не больше профилей, чем указано в Настройках → Сценарии; "
                   "уже запущенные профили используются как есть.")
        h.setObjectName("hint")
        h.setWordWrap(True)
        lay.addWidget(h)
        chosen: list[int] = []

        def validate():
            chosen[:] = [lst.item(i).data(Qt.UserRole) for i in range(lst.count()) if lst.item(i).checkState() == Qt.Checked]
            return None if chosen else "Отметьте хотя бы один профиль"

        if not Modal(self.win, "Запуск сценария", w, ok_text="Запустить", width=560, validate=validate).exec():
            return
        sid, name, hl, ca = self.current["id"], self.current.get("name"), headless.isChecked(), close_after.isChecked()
        self.win.log(f"Сценарий «{name}» запускается на профилях: {len(chosen)}")

        def done(res, err):
            if err:
                self.win.log(f"Сценарий «{name}»: {err}")
            else:
                ok = sum(1 for r in res if r.get("ok"))
                self.win.log(f"Сценарий «{name}» завершён: успешно {ok} из {len(res)}")
            self.win.refresh()

        run_bg(lambda: manager.run(rpa.jobs.run_many(manager, sid, chosen, hl, ca, log=manager.scheduler.say),
                                   timeout=None), done, self)
        self.refresh()

    def stop_all(self):
        manager.loop.call_soon_threadsafe(rpa.jobs.stop_all)
        self.win.log("Сценарии останавливаются…")

    # ---------- запись действиями ----------
    def toggle_record(self):
        if manager.recorder.active:
            def done(steps, err):
                if err:
                    self.win.log(f"Ошибка записи: {err}")
                    return
                if not steps:
                    self.win.log("Запись остановлена: действий не было")
                    self.refresh()
                    return
                if not self.current:
                    import time as _t
                    self.current = db.save_doc("scenarios", {"name": f"Запись {_t.strftime('%d.%m %H:%M')}", "steps": []})
                    self.name.setText(self.current["name"])
                self.current.setdefault("steps", []).extend(steps)
                self._save(len(self.current["steps"]) - 1)
                self.win.log(f"Записано шагов: {len(steps)} — добавлены в сценарий «{self.current['name']}». "
                             "Проверьте селекторы и паузы перед запуском на многих профилях.")
            run_bg(lambda: manager.run(manager.recorder.stop()), done, self)
            return
        profiles = db.list_profiles()
        if not profiles:
            self.win.log("Сначала создайте профиль")
            return
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        combo = QComboBox()
        pre = self.win.pages["profiles"].selected_ids()
        for p in profiles:
            combo.addItem(f"{'●' if manager.is_running(p.id) else '○'}  #{p.id}  {p.name}", p.id)
        if pre:
            combo.setCurrentIndex(max(0, combo.findData(pre[0])))
        hint = QLabel("Профиль откроется (если ещё не открыт). Работайте в нём как обычно — клики, ввод, Enter, "
                      "прокрутка, переходы и паузы станут шагами сценария. Затем нажмите «Остановить запись».\n\n"
                      "Пароли записываются как {{password}}, коды 2FA — как {{totp}}, логин аккаунта профиля — "
                      "как {{login}}: в сценарии не остаётся секретов.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(combo)
        lay.addWidget(hint)
        if not Modal(self.win, "Запись сценария", w, ok_text="Начать запись", width=520).exec():
            return
        pid = combo.currentData()
        self.win.log(f"Запись сценария в профиле #{pid}…")
        run_bg(lambda: manager.run(manager.recorder.start(pid)),
               lambda r, e: (self.win.log(f"Ошибка записи: {e}" if e else "Запись идёт — действуйте в окне профиля"),
                             self.refresh()), self)

    # ---------- таблица данных ----------
    def edit_data(self):
        from PySide6.QtWidgets import QPlainTextEdit
        if not self.current:
            return
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        text = QPlainTextEdit(self.current.get("data", ""))
        text.setPlaceholderText("profile;email;comment\n1;first@mail.com;Привет\nМой профиль 2;second@mail.com;Здравствуйте")
        text.setMinimumHeight(220)
        load = QPushButton("  Загрузить из файла…")
        set_icon(load, "upload", "text", 14)

        def from_file():
            from pathlib import Path
            from PySide6.QtWidgets import QFileDialog
            path, _ = QFileDialog.getOpenFileName(self, "CSV с данными", "", "CSV (*.csv *.txt);;Все (*)")
            if path:
                text.setPlainText(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
        load.clicked.connect(from_file)
        hint = QLabel("Первая строка — названия колонок, разделитель «;», «,» или табуляция. Каждая строка достаётся "
                      "одному профилю: по колонке profile (номер или название профиля), а без неё — по порядку "
                      "запуска. В шагах используйте {{название_колонки}}. Извлечённые шагом «Извлечь» значения "
                      "сохраняются в data/rpa_results.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(text)
        lay.addWidget(load)
        lay.addWidget(hint)

        def validate():
            try:
                rows = rpa.parse_data(text.toPlainText())
            except Exception as e:  # noqa: BLE001
                return f"Не удалось разобрать: {e}"
            return None if rows or not text.toPlainText().strip() else "Нет ни одной строки данных под заголовком"

        if Modal(self.win, f"Данные сценария «{self.current.get('name')}»", w, ok_text="Сохранить", width=640,
                 validate=validate).exec():
            self.current["data"] = text.toPlainText().strip()
            self._save()
            self.win.log(f"Данные сценария: строк {len(rpa.parse_data(self.current['data']))}")
