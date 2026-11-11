from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QMenu,
                               QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ... import db, settings
from ...manager import manager
from ...models import Proxy
from ...proxy.checker import check_proxy
from ..icons import icon
from ..modal import Modal, confirm
from ..theme import C
from ..widgets import empty_label, set_icon, fill_list, section, side_list, side_title, tool
from ..workers import run_bg

COLS = ["#", "Тип", "Адрес", "Логин", "Внешний IP", "Страна", "Таймзона", "Профилей", "Статус"]
WIDTHS = (44, 70, 0, 110, 130, 70, 150, 80, 120)
STATUS = {"ok": ("✓  Работает", "green"), "fail": ("✗  Ошибка", "red"), "": ("—  Не проверен", "faint")}


class ProxiesSidebar(QFrame):
    def __init__(self, page: "ProxiesPage"):
        super().__init__()
        self.setObjectName("sideBar")
        self.page = page
        self.filter: tuple[str, str] | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(side_title("ПРОКСИ",
                               tool("plus", "Добавить прокси", page.add_proxies),
                               tool("shield", "Проверить все", page.check_all),
                               tool("refresh", "Обновить", page.refresh)))
        self.status = side_list()
        self.countries = side_list()
        self.status.itemClicked.connect(lambda it: self._pick("status", it))
        self.countries.itemClicked.connect(lambda it: self._pick("country", it))
        v.addWidget(section("СОСТОЯНИЕ"))
        v.addWidget(self.status, 2)
        v.addWidget(section("СТРАНЫ"))
        v.addWidget(self.countries, 3)

    def _pick(self, kind, item):
        val = item.data(Qt.UserRole)
        self.filter = None if val is None else (kind, val)
        self.page.refresh()

    def update_lists(self, proxies):
        cnt = {"ok": 0, "fail": 0, "": 0}
        countries: dict[str, int] = {}
        for p in proxies:
            cnt[p.status] = cnt.get(p.status, 0) + 1
            if p.country:
                countries[p.country] = countries.get(p.country, 0) + 1
        f, val = self.filter or (None, None)
        fill_list(self.status, [(f"Все прокси   {len(proxies)}", None, "globe"),
                                (f"Работают   {cnt['ok']}", "ok", "check"),
                                (f"С ошибкой   {cnt['fail']}", "fail", "x"),
                                (f"Не проверены   {cnt['']}", "", "refresh")],
                  val if f == "status" else (None if not self.filter else "\0"))
        fill_list(self.countries, [(f"{c}   {n}", c, "globe") for c, n in sorted(countries.items())],
                  val if f == "country" else "\0")


class ProxiesPage(QWidget):
    key, title, icon_name = "proxies", "Прокси", "globe"
    search_hint = "Поиск прокси по адресу, IP, стране"

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.sidebar = ProxiesSidebar(self)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 10, 16, 10)
        hl.setSpacing(6)
        self.crumbs = QLabel()
        self.crumbs.setObjectName("crumbs")
        check = QPushButton("  Проверить выбранные")
        set_icon(check, "shield", "text", 14)
        check.clicked.connect(self.check_selected)
        add = QPushButton("  Добавить прокси")
        add.setObjectName("primary")
        add.setIcon(icon("plus", "#ffffff", 14))
        add.clicked.connect(self.add_proxies)
        for b in (check, add):
            b.setCursor(Qt.PointingHandCursor)
        hl.addWidget(self.crumbs)
        hl.addStretch()
        hl.addWidget(check)
        hl.addWidget(add)
        v.addWidget(head)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels([c.upper() for c in COLS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        QShortcut(QKeySequence(Qt.Key_Delete), self.table, activated=self.delete_selected, context=Qt.WidgetShortcut)
        v.addWidget(self.table, 1)
        self.empty = empty_label()
        v.addWidget(self.empty, 1)

        self.tab_actions = QWidget()
        al = QHBoxLayout(self.tab_actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(2)
        al.addStretch()
        al.addWidget(tool("shield", "Проверить все", self.check_all))
        al.addWidget(tool("trash", "Удалить выбранные (Del)", self.delete_selected))
        self.checking: set[int] = set()

    def refresh(self):
        proxies = db.list_proxies()
        self.sidebar.update_lists(proxies)
        usage: dict[int, int] = {}
        for p in db.list_profiles():
            if p.proxy_id is not None:
                usage[p.proxy_id] = usage.get(p.proxy_id, 0) + 1
        flt = self.sidebar.filter
        q = self.win.search.text().lower().strip()
        sel = set(self.selected_ids())

        def keep(p: Proxy) -> bool:
            if flt and flt[0] == "status" and p.status != flt[1]:
                return False
            if flt and flt[0] == "country" and p.country != flt[1]:
                return False
            return not q or q in " ".join([p.host, str(p.port), p.ip, p.country, p.username, p.type]).lower()

        rows = [p for p in proxies if keep(p)]
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for r, p in enumerate(rows):
            text, color = STATUS.get(p.status, STATUS[""])
            if p.id in self.checking:
                text, color = "…  Проверка", "muted"
            vals = [str(p.id), p.type.upper(), f"{p.host}:{p.port}", p.username or "—", p.ip or "—",
                    p.country or "—", p.timezone or "—", str(usage.get(p.id, 0)), text]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if c == 0:
                    item.setForeground(QColor(C["faint"]))
                elif c == 8:
                    item.setForeground(QColor(C[color]))
                    if p.error:
                        item.setToolTip(p.error)
                elif c in (3, 6, 7):
                    item.setForeground(QColor(C["muted"]))
                self.table.setItem(r, c, item)
            if p.id in sel:
                self.table.selectRow(r)
        self.table.blockSignals(False)
        self.table.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        self.empty.setText("Ничего не найдено по текущему фильтру." if proxies else
                           "Прокси пока нет.\nНажмите «Добавить прокси», чтобы вставить список.")
        self.crumbs.setText(f"Прокси  ›  {'Все' if not flt else flt[1] or 'Не проверены'}  ·  {len(rows)}")

    def selected_ids(self) -> list[int]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [int(self.table.item(r, 0).text()) for r in rows if self.table.item(r, 0)]

    def _context_menu(self, pos):
        ids = self.selected_ids()
        if not ids:
            return
        m = QMenu(self)
        for ico, text, fn in (("shield", "Проверить", self.check_selected), ("copy", "Копировать адрес", self.copy_selected),
                              (None, None, None), ("trash", "Удалить\tDel", self.delete_selected)):
            if ico is None:
                m.addSeparator()
                continue
            a = QAction(icon(ico, C["text"], 14), text, m)
            a.triggered.connect(fn)
            m.addAction(a)
        m.exec(self.table.viewport().mapToGlobal(pos))

    # ---------- действия ----------
    def add_proxies(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        hint = QLabel("По одному прокси в строке. Поддерживаются форматы:\n"
                      "type://user:pass@host:port   ·   host:port   ·   host:port:user:pass")
        hint.setObjectName("hint")
        text = QPlainTextEdit()
        text.setPlaceholderText("socks5://user:pass@1.2.3.4:1080\nhttp://5.6.7.8:3128")
        text.setFixedHeight(160)
        check = QCheckBox("Проверить сразу после добавления")
        check.setChecked(bool(settings.get("check_proxy_on_add")))
        lay.addWidget(hint)
        lay.addWidget(text)
        lay.addWidget(check)

        parsed: list[Proxy] = []

        def validate():
            parsed.clear()
            bad = []
            for i, line in enumerate(text.toPlainText().splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    parsed.append(Proxy.parse(line))
                except Exception:  # noqa: BLE001
                    bad.append(str(i))
            if bad:
                return f"Не удалось разобрать строки: {', '.join(bad[:10])}"
            if not parsed:
                return "Вставьте хотя бы один прокси"
            return None

        if not Modal(self.win, "Добавить прокси", w, ok_text="Добавить", width=560, validate=validate).exec():
            return
        settings.set("check_proxy_on_add", check.isChecked())
        ids = [db.save_proxy(p).id for p in parsed]
        self.win.log(f"Добавлено прокси: {len(ids)}")
        self.refresh()
        if check.isChecked():
            self._check(ids)

    def check_selected(self):
        ids = self.selected_ids()
        if not ids:
            self.win.log("Выберите прокси в таблице")
            return
        self._check(ids)

    def check_all(self):
        self._check([p.id for p in db.list_proxies()])

    def _check(self, ids: list[int]):
        ids = [i for i in ids if i not in self.checking]
        if not ids:
            return
        self.checking.update(ids)
        self.win.log(f"Проверка прокси: {len(ids)}…")
        self.refresh()

        def job():
            ok = 0
            for pid in ids:
                p = db.get_proxy(pid)
                if p:
                    ok += manager.run(check_proxy(p))["ok"]
                    db.save_proxy(p)
            return ok

        def done(res, err):
            self.checking.difference_update(ids)
            self.win.log(f"Ошибка проверки: {err}" if err else f"Проверено прокси: {len(ids)}, рабочих: {res}")
            self.refresh()

        run_bg(job, done, self)

    def copy_selected(self):
        lines = [db.get_proxy(i).url() for i in self.selected_ids() if db.get_proxy(i)]
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.win.log(f"Скопировано адресов: {len(lines)}")

    def delete_selected(self):
        ids = self.selected_ids()
        if not ids:
            self.win.log("Выберите прокси в таблице")
            return
        used = [p for p in db.list_profiles() if p.proxy_id in ids]
        text = f"Будет удалено прокси: {len(ids)}."
        if used:
            text += f"\nОни используются в профилях ({len(used)}): у этих профилей прокси будет снят."
        if settings.get("confirm_delete") and not confirm(self.win, "Удалить прокси?", text):
            return
        for p in used:
            p.proxy_id = None
            db.save_profile(p)
        for pid in ids:
            db.delete_proxy(pid)
        self.win.log(f"Удалено прокси: {len(ids)}")
        self.refresh()
