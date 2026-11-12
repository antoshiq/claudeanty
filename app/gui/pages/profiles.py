from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ... import cookies as ck
from ... import db, settings
from ...db import next_number  # noqa: F401 — используется и из других модулей GUI
from ...check import format_report, open_check_sites, run_check
from ...external_check import format_external, run_external
from ...fingerprints.generator import DEVICES
from ...manager import manager
from ...models import STATUSES, Profile, Proxy
from ...activity import summary as activity_summary
from ...warmplan import progress as warmplan_progress
from ...warmup import warmup
from ..icons import icon
from ..modal import Modal, confirm
from ..profile_bulk import QUICK, Onboarding, check_state, quick_match
from ..profile_form import profile_modal
from ..theme import C
from ..widgets import dot_icon, empty_label, fill_list, section, set_icon, side_list, side_title, tool
from ..workers import run_bg

COLS = ["#", "Название", "Метка", "Тип", "Движок", "Прокси", "Статус", "Папка", "Теги", "Проверка"]
WIDTHS = (44, 0, 110, 150, 90, 160, 115, 100, 110, 90)  # 0 = растягивается
OS_NAMES = {"windows": "Windows", "macos": "macOS", "linux": "Linux"}


class ProfilesSidebar(QFrame):
    def __init__(self, page: "ProfilesPage"):
        super().__init__()
        self.setObjectName("sideBar")
        self.page = page
        self.filter: tuple[str, str] | None = None  # ("folder"|"tag"|"status", значение)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(side_title("ПРОФИЛИ",
                               tool("plus", "Новый профиль (Ctrl+N)", page.new_profile),
                               tool("copy", "Создать массово", page.bulk_create),
                               tool("folder_open", "Импорт: аккаунты строками, файл claudeAnty, Dolphin Anty, AdsPower, таблица",
                                    page.import_menu),
                               tool("refresh", "Обновить (F5)", page.refresh)))
        self.folders = side_list()
        self.statuses = side_list()
        self.tags = side_list()
        self.quick = side_list()
        for lst, kind in ((self.folders, "folder"), (self.statuses, "status"), (self.tags, "tag"),
                          (self.quick, "quick")):
            lst.itemClicked.connect(lambda it, k=kind: self._pick(k, it))
        v.addWidget(section("БЫСТРЫЕ ФИЛЬТРЫ", self.quick, "profiles.quick"))
        v.addWidget(self.quick, 2)
        v.addWidget(section("ПАПКИ", self.folders, "profiles.folders"))
        v.addWidget(self.folders, 3)
        v.addWidget(section("МЕТКИ", self.statuses, "profiles.statuses"))
        v.addWidget(self.statuses, 2)
        v.addWidget(section("ТЕГИ", self.tags, "profiles.tags"))
        v.addWidget(self.tags, 2)
        v.addStretch(0)  # забирает место, когда все разделы свёрнуты — заголовки остаются наверху

    def _pick(self, kind, item):
        val = item.data(Qt.UserRole)
        new = None if val is None else (kind, val)
        self.filter = None if new == self.filter and kind == "quick" else new  # повторный клик снимает быстрый фильтр
        self.page.refresh()

    def update_lists(self, profiles, quick_counts: dict | None = None):
        folders: dict[str, int] = {}
        tags: dict[str, int] = {}
        statuses: dict[str, int] = {}
        for p in profiles:
            if p.folder:
                folders[p.folder] = folders.get(p.folder, 0) + 1
            for t in p.tags:
                tags[t] = tags.get(t, 0) + 1
            statuses[p.status] = statuses.get(p.status, 0) + 1
        f, t = self.filter or (None, None)
        fill_list(self.folders, [(f"Все профили   {len(profiles)}", None, "layers")] +
                  [(f"{name}   {n}", name, "folder") for name, n in sorted(folders.items())],
                  t if f == "folder" else (None if not self.filter else "\0"))
        fill_list(self.statuses, [(f"{STATUSES[k][0]}   {statuses[k]}", k, STATUSES[k][1])
                                  for k in STATUSES if statuses.get(k)], t if f == "status" else "\0")
        fill_list(self.tags, [(f"{name}   {n}", name, "tag") for name, n in sorted(tags.items())],
                  t if f == "tag" else "\0")
        qc = quick_counts or {}
        fill_list(self.quick, [(f"{QUICK[k][0]}   {qc[k]}", k, QUICK[k][1]) for k in QUICK if qc.get(k)],
                  t if f == "quick" else "\0")


class ProfilesPage(QWidget):
    key, title, icon_name = "profiles", "Профили", "users"
    search_hint = "Поиск профилей по названию, тегам, заметкам"

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.sidebar = ProfilesSidebar(self)
        self.busy: dict[int, str] = {}  # pid -> «Проверка…» / «Прогрев…»

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 10, 16, 10)
        hl.setSpacing(6)
        self.crumbs = QLabel()
        self.crumbs.setObjectName("crumbs")
        stop = QPushButton("  Остановить")
        set_icon(stop, "stop", "text", 12)
        stop.clicked.connect(self.stop_selected)
        start = QPushButton("  Запустить")
        start.setObjectName("primary")
        start.setIcon(icon("play", "#ffffff", 12))
        start.clicked.connect(self.start_selected)
        for b in (stop, start):
            b.setCursor(Qt.PointingHandCursor)
        more = QPushButton("  Действия")
        set_icon(more, "layers", "text", 14)
        more.setCursor(Qt.PointingHandCursor)
        more.setToolTip("Все действия с выбранными профилями — с подписями (то же, что правый клик по таблице)")
        more.clicked.connect(lambda: self._build_menu().exec(more.mapToGlobal(more.rect().bottomLeft())))
        hl.addWidget(self.crumbs)
        hl.addStretch()
        hl.addWidget(more)
        hl.addWidget(stop)
        hl.addWidget(start)
        v.addWidget(head)

        # панель массовых действий: видна, когда выбрано несколько профилей
        self.bulk_bar = QFrame()
        self.bulk_bar.setObjectName("settingRow")
        bb = QHBoxLayout(self.bulk_bar)
        bb.setContentsMargins(16, 6, 12, 6)
        bb.setSpacing(6)
        self.bulk_label = QLabel()
        self.bulk_label.setObjectName("settingTitle")
        bb.addWidget(self.bulk_label)
        bb.addSpacing(8)
        status_btn = QPushButton("  Метка")
        set_icon(status_btn, "tag", "text", 14)
        smenu = QMenu(status_btn)
        for key, (label, color) in STATUSES.items():
            a = QAction(dot_icon(color), label, smenu)
            a.triggered.connect(lambda _=False, k=key: self.set_status(k))
            smenu.addAction(a)
        status_btn.setMenu(smenu)
        for text, ico, fn in (("  В папку", "folder", self.folder_selected), ("  Теги", "tag", self.tags_selected),
                              (None, None, status_btn), ("  Прокси", "globe", self.assign_proxies),
                              ("  План прогрева", "flame", self.plan_selected),
                              ("  Проверить", "fingerprint", self.check_selected)):
            if text is None:
                b = fn
            else:
                b = QPushButton(text)
                set_icon(b, ico, "text", 14)
                b.clicked.connect(fn)
            b.setCursor(Qt.PointingHandCursor)
            bb.addWidget(b)
        bb.addStretch()
        delete = QPushButton("  Удалить")
        set_icon(delete, "trash", "red", 14)
        delete.clicked.connect(self.delete_selected)
        clear = QPushButton("Снять выбор")
        clear.clicked.connect(lambda: self.table.clearSelection())
        for b in (delete, clear):
            b.setCursor(Qt.PointingHandCursor)
            bb.addWidget(b)
        self.bulk_bar.hide()
        v.addWidget(self.bulk_bar)

        # подсказка «начало работы», пока не пройдены все шаги
        self.tip = QFrame()
        self.tip.setObjectName("settingRow")
        tl = QHBoxLayout(self.tip)
        tl.setContentsMargins(16, 6, 8, 6)
        self.tip_text = QLabel()
        self.tip_text.setObjectName("hint")
        self.tip_btn = QPushButton()
        self.tip_btn.setObjectName("primary")
        self.tip_btn.setCursor(Qt.PointingHandCursor)
        self.tip_btn.clicked.connect(lambda: self._tip_action())
        tip_x = tool("x", "Скрыть подсказку", lambda: (settings.set("onboarding_done", True), self.refresh()), 14)
        tl.addWidget(self.tip_text, 1)
        tl.addWidget(self.tip_btn)
        tl.addWidget(tip_x)
        self.tip.hide()
        v.addWidget(self.tip)

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
        hh.setStretchLastSection(False)
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for i, w in enumerate(WIDTHS):
            if w:
                self.table.setColumnWidth(i, w)
            else:
                hh.setSectionResizeMode(i, QHeaderView.Stretch)
        self.table.doubleClicked.connect(self.start_selected)
        self.table.itemSelectionChanged.connect(self.show_details)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        for keys, fn in ((Qt.Key_Return, self.start_selected), (Qt.Key_Enter, self.start_selected),
                         (Qt.Key_Delete, self.delete_selected), (Qt.Key_F2, self.edit_selected)):
            QShortcut(QKeySequence(keys), self.table, activated=fn, context=Qt.WidgetShortcut)
        v.addWidget(self.table, 1)
        self.empty = empty_label()
        v.addWidget(self.empty, 1)
        self.onboarding = Onboarding(self)
        v.addWidget(self.onboarding, 1)

        # действия редактора (справа от вкладок)
        self.tab_actions = QWidget()
        al = QHBoxLayout(self.tab_actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(2)
        al.addStretch()
        def sep():
            line = QFrame()
            line.setFixedSize(1, 16)
            line.setStyleSheet(f"background: {C['border']};")
            al.addSpacing(4)
            al.addWidget(line)
            al.addSpacing(4)

        al.addWidget(tool("fingerprint", "Проверить отпечаток", self.check_selected))
        al.addWidget(tool("shield", "Отчёт внешних детекторов (pixelscan, CreepJS, fingerprint.com, browserleaks)",
                          self.external_selected))
        al.addWidget(tool("activity", "Здоровье профилей: история проверок на графике", self.health_selected))
        al.addWidget(tool("clock", "Журнал активности профиля", self.activity_selected))
        sep()
        al.addWidget(tool("grid", "Разложить окна запущенных профилей сеткой", self.arrange_windows))
        al.addWidget(tool("key", "Пароли и 2FA профиля", self.accounts_selected))
        al.addWidget(tool("flame", "Прогреть cookies", self.warmup_selected))
        sep()
        al.addWidget(tool("upload", "Импорт cookies в выбранный профиль", self.import_cookies))
        al.addWidget(tool("download", "Экспорт cookies выбранного профиля", self.export_cookies))
        sep()
        al.addWidget(tool("edit", "Изменить профиль (F2)", self.edit_selected))
        al.addWidget(tool("trash", "Удалить (Del)", self.delete_selected))

    # ---------- данные ----------
    def refresh(self):
        profiles = db.list_profiles()
        proxies = {p.id: p for p in db.list_proxies()}
        qc = {k: sum(1 for p in profiles if quick_match(k, p, proxies, manager.is_running(p.id))) for k in QUICK}
        self.sidebar.update_lists(profiles, qc)
        flt = self.sidebar.filter
        q = self.win.search.text().lower().strip()
        sel = set(self.selected_ids())

        def keep(p: Profile) -> bool:
            if flt and flt[0] == "folder" and p.folder != flt[1]:
                return False
            if flt and flt[0] == "tag" and flt[1] not in p.tags:
                return False
            if flt and flt[0] == "status" and p.status != flt[1]:
                return False
            if flt and flt[0] == "quick" and not quick_match(flt[1], p, proxies, manager.is_running(p.id)):
                return False
            return not q or q in " ".join([p.name, p.notes, *p.tags]).lower()

        rows = [p for p in profiles if keep(p)]
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for r, p in enumerate(rows):
            px = proxies.get(p.proxy_id)
            on = manager.is_running(p.id)
            if p.kind == "mobile":
                kind_text, kind_icon = DEVICES.get(p.device, {}).get("label", "Android"), "phone"
            else:
                kind_text, kind_icon = OS_NAMES.get(p.os, p.os), "monitor"
            st_label, st_color = STATUSES.get(p.status, STATUSES[""])
            state = self.busy.get(p.id) or ("●  Работает" if on else "○  Остановлен")
            vals = [str(p.id), p.name, st_label if p.status else "—", kind_text,
                    "Chromium" if p.engine == "chromium" else "Camoufox",
                    f"{px.host}:{px.port}  {px.country}".strip() if px else ("⚠ удалён" if p.proxy_id else "—"), state, p.folder, ", ".join(p.tags)]
            chk_text, chk_color, chk_tip, _ = check_state(p)
            vals.append(chk_text)
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if c == 0:
                    item.setForeground(QColor(C["faint"]))
                elif c == 2:
                    if p.status:
                        item.setIcon(dot_icon(st_color))
                        if p.status == "ban" and p.ban_reason:
                            item.setToolTip(f"Метка поставлена автоматически: {p.ban_reason}")
                    else:
                        item.setForeground(QColor(C["faint"]))
                elif c == 3:
                    item.setIcon(icon(kind_icon, C["muted"], 14))
                elif c == 6:
                    item.setForeground(QColor(C["accent"] if p.id in self.busy else C["green"] if on else C["faint"]))
                elif c == 5 and p.proxy_id and not px:
                    item.setForeground(QColor(C["red"]))
                    item.setToolTip("Прокси удалён — профиль не запустится, пока не назначите новый")
                elif c == 5 and px and px.status == "fail":
                    item.setForeground(QColor(C["red"]))
                    item.setToolTip(f"Прокси не работает: {px.error[:150]}")
                elif c in (5, 7, 8):
                    item.setForeground(QColor(C["muted"]))
                elif c == 9:
                    item.setForeground(QColor(chk_color))
                    item.setToolTip(chk_tip)
                self.table.setItem(r, c, item)
        from ..widgets import restore_selection
        restore_selection(self.table, [r for r, p in enumerate(rows) if p.id in sel])
        self.table.blockSignals(False)

        self.table.setVisible(bool(rows))
        self.empty.setVisible(not rows and bool(profiles))
        self.empty.setText("Ничего не найдено по текущему фильтру.")
        self.onboarding.setVisible(not profiles)
        if not profiles:
            self.onboarding.update_state()
        self._update_tip(profiles, proxies)
        self._selection_changed()
        if not flt:
            where = "Все профили"
        elif flt[0] == "quick":
            where = QUICK[flt[1]][0]
        elif flt[0] == "status":
            where = "Метка: " + STATUSES.get(flt[1], ("?",))[0]
        else:
            where = ("Папка: " if flt[0] == "folder" else "Тег: ") + flt[1]
        self.crumbs.setText(f"Профили  ›  {where}  ·  {len(rows)}")

    def selected_ids(self) -> list[int]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [int(self.table.item(r, 0).text()) for r in rows if self.table.item(r, 0)]

    def _need(self) -> list[int]:
        ids = self.selected_ids()
        if not ids:
            self.win.log("Выберите профиль в таблице")
        return ids

    def show_details(self):
        ids = self.selected_ids()
        p = db.get_profile(ids[0]) if ids else None
        if not p:
            self.win.set_details("Выберите профиль, чтобы увидеть его отпечаток.")
            return
        from ...engines.chromium import build_fp
        px = db.get_proxy(p.proxy_id)
        if p.auto_geo and not p.timezone:
            tz = "(по IP прокси или вашему IP)"
        else:
            tz = p.timezone or "(системная)"
        cf = p.fingerprint.get("camoufox") if p.engine == "camoufox" else None
        if cf:
            nav, scr, win = cf.get("navigator", {}), cf.get("screen", {}), cf.get("window", {})
            os_name = cf.get("client", {}).get("os", {}).get("alias", p.os)
            ua = f"Firefox (Camoufox) на {os_name}, версия подставляется при запуске"
            platform, gpu = nav.get("platform"), cf.get("gpu", {}).get("renderer")
            screen = f"{scr.get('width')}x{scr.get('height')} @{win.get('devicePixelRatio', 1)}x"
            cpu = f"{nav.get('hardwareConcurrency')} cores"
            touch = cf.get("windowFeatures", {}).get("maxTouchPoints", 0)
        else:
            fp = build_fp(p)  # как уйдёт в браузер: с реальной версией Chrome
            sc = fp.get("screen", {})
            ua, platform, gpu = fp.get("user_agent"), fp.get("platform"), fp.get("webgl", {}).get("renderer")
            screen = f"{sc.get('width')}x{sc.get('height')} @{sc.get('dpr')}x" if sc else None
            cpu = f"{fp.get('cores')} cores / {fp.get('memory')} GB" if fp.get("cores") else None
            touch = fp.get("touch_points")
        info = [
            ("profile", f"#{p.id} {p.name}"), ("type", "mobile" if p.kind == "mobile" else "desktop"),
            ("engine", p.engine), ("userAgent", ua), ("platform", platform), ("screen", screen),
            ("machine", p.fingerprint.get("machine")), ("webgl", gpu), ("cpu / ram", cpu), ("touch", touch), ("locale", p.locale), ("timezone", tz),
            ("webrtc", {"proxy": "только через прокси", "off": "отключён", "real": "как есть"}[p.webrtc]),
            ("proxy", f"{px.type}://{px.host}:{px.port} {px.country}" if px else "нет"),
            ("start", ", ".join(p.start_urls) or None), ("extensions", str(len(p.extensions)) if p.extensions else None),
            ("notes", p.notes.replace("\n", " ") or None),
            ("accounts", self._accounts_line(p.id)),
            ("ban", p.ban_reason if p.status == "ban" and p.ban_reason else None),
            ("warm plan", warmplan_progress(p) or None),
            ("activity", activity_summary(p.id) or None),
        ]
        text = "\n".join(f"{k:<11} {v}" for k, v in info if v not in (None, ""))
        if p.last_check:
            text += "\n\n" + format_report(p.last_check)
        else:
            text += "\n\nОтпечаток ещё не проверялся — кнопка с отпечатком пальца вверху справа."
        if p.last_external:
            text += "\n\n" + format_external(p.last_external)
        else:
            text += "\nОтчёт внешних детекторов — кнопка со щитом (pixelscan, CreepJS, fingerprint.com, browserleaks)."
        self.win.set_details(text)

    def _context_menu(self, pos):
        if self.selected_ids():
            self._build_menu().exec(self.table.viewport().mapToGlobal(pos))

    def _build_menu(self) -> QMenu:
        m = QMenu(self)

        def add(menu, ico, text, fn, keys=""):
            a = QAction(icon(ico, C["text"], 14) if ico else dot_icon("#00000000"), text + (f"\t{keys}" if keys else ""), menu)
            a.triggered.connect(fn)
            menu.addAction(a)
            return a

        add(m, "play", "Запустить", self.start_selected, "Enter")
        add(m, "stop", "Остановить", self.stop_selected)
        add(m, "grid", "Разложить окна…", self.arrange_windows)
        m.addSeparator()
        add(m, "edit", "Изменить…", self.edit_selected, "F2")
        add(m, "copy", "Создать копии…", self.bulk_create)
        st = m.addMenu(dot_icon(C["accent"]), "Метка")
        for key, (label, color) in STATUSES.items():
            a = QAction(dot_icon(color), label, st)
            a.triggered.connect(lambda _=False, k=key: self.set_status(k))
            st.addAction(a)
        add(m, "folder", "В папку…", self.folder_selected)
        add(m, "tag", "Теги…", self.tags_selected)
        add(m, "globe", "Назначить прокси…", self.assign_proxies)
        add(m, "key", "Пароли и 2FA…", self.accounts_selected)
        m.addSeparator()
        add(m, "fingerprint", "Проверить отпечаток", self.check_selected)
        add(m, "shield", "Отчёт внешних детекторов", self.external_selected)
        add(m, "repeat", "Проверить стабильность отпечатка", self.stability_selected)
        add(m, "activity", "Здоровье профилей…", self.health_selected)
        add(m, "users", "Уникальность профилей…", self.uniqueness_selected)
        add(m, "activity", "Статистика банов…", self.banstats)
        add(m, "clock", "Журнал активности…", self.activity_selected)
        add(m, "external", "Открыть сайты проверки", self.open_sites)
        add(m, "flame", "Прогреть cookies", self.warmup_selected)
        add(m, "flame", "План прогрева…", self.plan_selected)
        m.addSeparator()
        add(m, "upload", "Импорт cookies…", self.import_cookies)
        add(m, "download", "Экспорт cookies…", self.export_cookies)
        m.addSeparator()
        add(m, "download", "Экспорт профилей в файл…", self.export_profiles)
        add(m, "key", "Передать профиль (файл с паролем)…", self.transfer_profiles)
        add(m, "folder_open", "Импорт профилей из файла…", self.import_profiles)
        add(m, "folder_open", "Импорт из Dolphin / AdsPower / таблицы…", self.import_external)
        add(m, "key", "Импорт аккаунтов строками…", self.import_accounts)
        m.addSeparator()
        add(m, "trash", "Удалить", self.delete_selected, "Del")
        if not self.selected_ids():  # из кнопки «Действия» без выбора — только то, что не требует профиля
            for a in m.actions():
                if a.text().split("\t")[0] not in ("Импорт профилей из файла…", "Импорт из Dolphin / AdsPower / таблицы…",
                                                    "Импорт аккаунтов строками…", "Статистика банов…",
                                                    "Разложить окна…",
                                                    "Уникальность профилей…", "Здоровье профилей…", "Создать копии…"):
                    a.setEnabled(False)
        return m

    # ---------- действия ----------
    def new_profile(self):
        p = profile_modal(self.win)
        if p:
            p = manager.create_profile(p)
            self.win.log(f"Создан профиль #{p.id} «{p.name}»")
            self.refresh()

    def edit_selected(self):
        ids = self._need()
        if not ids:
            return
        p = profile_modal(self.win, db.get_profile(ids[0]))
        if p:
            if p.status != "ban":
                p.ban_reason, p.captcha_streak = "", {}
            manager._prepare_fingerprint(p)
            db.save_profile(p)
            self.win.log(f"Профиль #{p.id} сохранён" + (" — изменения применятся при следующем запуске"
                                                         if manager.is_running(p.id) else ""))
            self.refresh()
            self.show_details()

    def bulk_create(self):
        from ..profile_bulk import bulk_create
        ids = self.selected_ids()
        if bulk_create(self.win, db.get_profile(ids[0]) if ids else None, manager, self.win.log):
            self.refresh()

    def delete_selected(self):
        ids = self._need()
        if not ids:
            return
        if settings.get("confirm_delete") and not confirm(
                self.win, "Удалить профили?",
                f"Будет удалено профилей: {len(ids)}. Cookies, история и данные браузера будут стёрты без возможности восстановления."):
            return
        for pid in ids:
            manager.delete_profile(pid)
        self.win.log(f"Удалено профилей: {len(ids)}")
        self.refresh()

    def set_status(self, key: str):
        ids = self._need()
        for pid in ids:
            p = db.get_profile(pid)
            if p:
                if key == "ban" and p.status != "ban":
                    from ...notify import notify
                    notify("ban", f"Профиль #{p.id} {p.name}" + (f" (папка {p.folder})" if p.folder else ""))
                if key != "ban":  # бан снят вручную — счётчики капч с нуля, иначе метка вернётся при первой капче
                    p.ban_reason, p.captcha_streak = "", {}
                if key != p.status:
                    from ...activity import log as activity_log
                    activity_log(p.id, "ban" if "ban" in (key, p.status) else "edit",
                                 f"метка «{STATUSES.get(p.status, ('—',))[0]}» → «{STATUSES[key][0]}» вручную")
                old = p.status
                p.status = key
                db.save_profile(p)
                from ...banstats import status_changed
                status_changed(p, old, key, "manual")
        self.win.log(f"Метка «{STATUSES[key][0]}» — профилей: {len(ids)}")
        self.refresh()

    def assign_proxies(self):
        ids = self._need()
        if not ids:
            return
        proxies = db.list_proxies()
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        mode = QComboBox()
        mode.addItem("Распределить выбранные прокси по кругу", "round")
        mode.addItem("Один прокси для всех", "one")
        mode.addItem("Вставить новый список (по одному на профиль)", "paste")
        mode.addItem("Снять прокси", "none")
        lst = QListWidget()
        lst.setObjectName("boxList")
        lst.setFixedHeight(150)
        for px in proxies:
            it = QListWidgetItem(f"{ {'ok': '✓', 'fail': '✗'}.get(px.status, '—') }  {px.type}://{px.host}:{px.port}  {px.country}"
                                 + (f"  · {px.tcp_os}" if px.tcp_os else ""))
            it.setData(Qt.UserRole, px.id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if px.status != "fail" else Qt.Unchecked)
            lst.addItem(it)
        text = QPlainTextEdit()
        text.setPlaceholderText("socks5://user:pass@host:port — по одному в строке")
        text.setFixedHeight(120)
        hint = QLabel()
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(QLabel(f"Профилей выбрано: {len(ids)}"))
        lay.addWidget(mode)
        lay.addWidget(lst)
        lay.addWidget(text)
        lay.addWidget(hint)

        def sync_mode():
            md = mode.currentData()
            lst.setVisible(md in ("round", "one"))
            text.setVisible(md == "paste")
            lst.setSelectionMode(QAbstractItemView.SingleSelection)
            hint.setText({"round": "Отмеченные прокси раздаются профилям по очереди: 1-му первый, 2-му второй и т.д.",
                          "one": "Всем выбранным профилям — первый отмеченный прокси.",
                          "paste": "Новые прокси сохраняются в список и назначаются профилям по порядку.",
                          "none": "У выбранных профилей прокси будет снят."}[md])

        mode.currentIndexChanged.connect(lambda _: sync_mode())
        sync_mode()
        chosen: list[int] = []

        def validate():
            chosen.clear()
            md = mode.currentData()
            if md in ("round", "one"):
                chosen.extend(lst.item(i).data(Qt.UserRole) for i in range(lst.count())
                              if lst.item(i).checkState() == Qt.Checked)
                if not chosen:
                    return "Отметьте хотя бы один прокси" if proxies else "Сначала добавьте прокси на вкладке «Прокси»"
            if md == "paste":
                try:
                    lines = [ln for ln in text.toPlainText().splitlines() if ln.strip()]
                    parsed = [Proxy.parse(ln) for ln in lines]
                except Exception:  # noqa: BLE001
                    return "Не удалось разобрать одну из строк"
                if not parsed:
                    return "Вставьте хотя бы один прокси"
                chosen.extend(db.save_proxy(px).id for px in parsed)
            return None

        if not Modal(self.win, "Назначить прокси", w, ok_text="Назначить", width=560, validate=validate).exec():
            return
        md = mode.currentData()
        for i, pid in enumerate(ids):
            p = db.get_profile(pid)
            if not p:
                continue
            if md == "none":
                p.proxy_id = None
            elif md == "one":
                p.proxy_id = chosen[0]
            else:
                p.proxy_id = chosen[i % len(chosen)]
            p.timezone = "" if p.auto_geo else p.timezone  # гео пересчитается по новому прокси
            db.save_profile(p)
        self.win.log(f"Прокси назначены профилям: {len(ids)}")
        self.refresh()

    def start_selected(self):
        embed_on = bool(settings.get("embed_browser"))
        ids = self._need()
        bare = [p for i in ids if not manager.is_running(i) and (p := db.get_profile(i)) and p.proxy_id is None]
        if bare and settings.get("warn_no_proxy"):
            names = ", ".join(f"#{p.id} {p.name}" for p in bare[:5]) + ("…" if len(bare) > 5 else "")
            if not confirm(self.win, "Запустить без прокси?",
                           f"Без прокси: {names}.\n\nСайты увидят ваш настоящий IP, и все такие профили окажутся на "
                           "одном адресе — по нему их легко связать.\n\nОтключить этот вопрос: Настройки → Поведение.",
                           ok_text="Запустить всё равно", danger=True):
                return
        for pid in ids:
            if manager.is_running(pid):
                if embed_on:
                    self.win.embed_profile(pid)  # уже запущен — открыть/показать его вкладку
                continue
            self.win.log(f"Запуск профиля #{pid}…")

            def done(res, err, pid=pid):
                self.win.log(f"Ошибка запуска #{pid}: {err}" if err else f"Профиль #{pid} запущен")
                self.refresh()
                if not err and embed_on:
                    self.win.embed_profile(pid)

            run_bg(lambda pid=pid: manager.run(manager.start(pid)), done, self)

    def stop_selected(self):
        for pid in self._need():
            def done(res, err, pid=pid):
                self.win.log(f"Ошибка остановки #{pid}: {err}" if err else f"Профиль #{pid} остановлен")
                self.refresh()

            run_bg(lambda pid=pid: manager.run(manager.stop(pid)), done, self)

    def check_selected(self):
        for pid in self._need():
            if pid in self.busy:
                continue
            self.busy[pid] = "…  Проверка"
            self.win.log(f"Проверка отпечатка #{pid}…")

            def done(res, err, pid=pid):
                self.busy.pop(pid, None)
                if err:
                    self.win.log(f"Ошибка проверки #{pid}: {err}")
                else:
                    self.win.log(f"Проверка #{pid}: {res['ok']} из {res['total']} в порядке"
                                 + (f", проблем: {res['fail']}" if res["fail"] else ""))
                    self.win.panel.setCurrentWidget(self.win.details)
                self.refresh()
                self.show_details()

            run_bg(lambda pid=pid: manager.run(run_check(manager, pid), timeout=300), done, self)
        self.refresh()

    def external_selected(self):
        for pid in self._need():
            if pid in self.busy:
                continue
            self.busy[pid] = "…  Детекторы"
            self.win.log(f"Отчёт внешних детекторов #{pid}: pixelscan, CreepJS, fingerprint.com, browserleaks "
                         "(около двух минут)…")

            def done(res, err, pid=pid):
                self.busy.pop(pid, None)
                if err:
                    self.win.log(f"Ошибка отчёта детекторов #{pid}: {err}")
                else:
                    self.win.log(f"Детекторы #{pid}: ✓ {res['ok']}, ! {res['warn']}, ✗ {res['fail']} — подробности "
                                 "в панели «Отпечаток»")
                    self.win.panel.setCurrentWidget(self.win.details)
                self.refresh()
                self.show_details()

            run_bg(lambda pid=pid: manager.run(run_external(manager, pid), timeout=900), done, self)
        self.refresh()

    def open_sites(self):
        for pid in self._need():
            self.win.log(f"Открываю CreepJS, pixelscan, fingerprint.com и browserleaks в профиле #{pid}…")
            run_bg(lambda pid=pid: manager.run(open_check_sites(manager, pid)),
                   lambda r, e, pid=pid: (self.win.log(f"Ошибка: {e}") if e else None, self.refresh()), self)

    def warmup_selected(self):
        ids = self._need()
        for pid in ids:
            if pid in self.busy:
                continue
            self.busy[pid] = "…  Прогрев"
            self.win.log(f"Прогрев cookies #{pid} (сайтов: {settings.get('warmup_count')})…")

            def done(res, err, pid=pid):
                self.busy.pop(pid, None)
                self.win.log(f"Ошибка прогрева #{pid}: {err}" if err else
                             f"Прогрев #{pid} завершён: сайтов {res['visited']}, cookies {res['cookies']}")
                self.refresh()

            run_bg(lambda pid=pid: manager.run(warmup(manager, pid), timeout=3600), done, self)
        self.refresh()

    def import_cookies(self):
        ids = self._need()
        if not ids:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Файл cookies (JSON или Netscape)", "", "Cookies (*.json *.txt);;Все (*)")
        if not path:
            return
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        self.win.log(f"Импорт cookies в #{ids[0]}…")
        run_bg(lambda: manager.run(manager.set_cookies(ids[0], text)),
               lambda res, err: self.win.log(f"Ошибка импорта: {err}" if err else f"Импортировано cookies: {res}"), self)

    def export_cookies(self):
        ids = self._need()
        if not ids:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить cookies", f"cookies_{ids[0]}.json",
                                              "JSON (*.json);;Netscape (*.txt)")
        if not path:
            return
        self.win.log(f"Экспорт cookies из #{ids[0]}…")

        def job():
            cookies = manager.run(manager.get_cookies(ids[0]))
            text = ck.export_netscape(cookies) if path.endswith(".txt") else ck.export_json(cookies)
            Path(path).write_text(text, encoding="utf-8")
            return len(cookies)

        run_bg(job, lambda res, err: self.win.log(f"Ошибка экспорта: {err}" if err else
                                                  f"Экспортировано cookies: {res} → {path}"), self)

    # ---------- экспорт/импорт профилей одним файлом ----------
    def export_profiles(self, transfer: bool = False):
        from PySide6.QtWidgets import QCheckBox, QLineEdit
        from ...backup import EXT, export_profiles
        ids = self._need()
        if not ids:
            return
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        info = QLabel(f"Профилей: {len(ids)}. В файл попадут настройки и отпечаток, прокси, cookies и данные браузера "
                      "(история, localStorage, IndexedDB, расширения) — без кэшей. Файл можно импортировать на другом ПК.")
        info.setWordWrap(True)
        mode = QComboBox()
        mode.addItem("Зашифровать паролем — для передачи другому человеку или на другой ПК", "password")
        mode.addItem("Без шифрования", "none")
        mode.setCurrentIndex(0 if transfer else 1)
        pw1, pw2 = QLineEdit(), QLineEdit()
        for ed, ph in ((pw1, "Пароль (от 8 символов)"), (pw2, "Повторите пароль")):
            ed.setEchoMode(QLineEdit.Password)
            ed.setPlaceholderText(ph)
        secrets = QCheckBox("Включить пароли и 2FA-секреты аккаунтов (Пароли и 2FA)")
        warn = QLabel()
        warn.setObjectName("hint")
        warn.setWordWrap(True)

        def sync(*_):
            enc = mode.currentData() == "password"
            pw1.setVisible(enc)
            pw2.setVisible(enc)
            warn.setText("Всё содержимое файла — cookies, пароли, история — шифруется (AES-256). Пароль сообщите "
                         "получателю отдельно от файла (не в том же чате). Без пароля файл не открыть."
                         if enc else "Cookies и (если отмечено) пароли и 2FA будут в файле открытым текстом — "
                         "храните его как пароль.")
            if enc and transfer:
                secrets.setChecked(True)

        mode.currentIndexChanged.connect(sync)
        lay.addWidget(info)
        lay.addWidget(mode)
        lay.addWidget(pw1)
        lay.addWidget(pw2)
        lay.addWidget(secrets)
        lay.addWidget(warn)
        sync()

        def validate():
            if mode.currentData() != "password":
                return None
            if len(pw1.text()) < 8:
                return "Пароль — не короче 8 символов"
            if pw1.text() != pw2.text():
                return "Пароли не совпадают"
            return None

        if not Modal(self.win, "Передать профиль" if transfer else "Экспорт профилей", w, ok_text="Выбрать файл…",
                     width=560, validate=validate).exec():
            return
        password = pw1.text() if mode.currentData() == "password" else ""
        name = db.get_profile(ids[0]).name if len(ids) == 1 else f"profiles_{len(ids)}"
        safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in name).strip() or "profile"
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить профили", safe + EXT, f"Профили claudeAnty (*{EXT})")
        if not path:
            return
        if not path.endswith(EXT):
            path += EXT
        inc = secrets.isChecked()
        self.win.log(f"Экспорт профилей ({len(ids)}) в {path}…")

        def done(r, e):
            if e:
                self.win.log(f"Ошибка экспорта: {e}")
                return
            self.win.log(f"Экспортировано профилей: {r['profiles']}, {r['size'] / 1e6:.1f} МБ → {r['path']}"
                         + (f" (пропущено занятых файлов: {r['skipped_files']})" if r["skipped_files"] else ""))

        run_bg(lambda: manager.run(export_profiles(manager, ids, path, include_secrets=inc, password=password),
                                   timeout=3600), done, self)

    def transfer_profiles(self):
        self.export_profiles(transfer=True)

    def import_profiles(self, path: str = ""):
        from PySide6.QtWidgets import QLineEdit
        from ...backup import EXT, encryption, import_archive, read_manifest
        from ...crypto import PasswordError
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Импорт профилей", "", f"Профили claudeAnty (*{EXT} *.zip);;Все (*)")
        if not path:
            return
        password, m, err = "", None, ""
        while m is None:
            if encryption(path) == "password":
                ed = QLineEdit()
                ed.setEchoMode(QLineEdit.Password)
                ed.setPlaceholderText("Пароль файла")
                box = QWidget()
                bl = QVBoxLayout(box)
                bl.setContentsMargins(0, 0, 0, 0)
                hint = QLabel((err + ". " if err else "") + "Файл зашифрован паролем — его сообщает тот, кто передал файл.")
                hint.setObjectName("modalError" if err else "hint")
                hint.setWordWrap(True)
                bl.addWidget(ed)
                bl.addWidget(hint)
                ed.setFocus()
                if not Modal(self.win, "Пароль файла", box, ok_text="Открыть", width=460).exec():
                    return
                password = ed.text()
            try:
                m = read_manifest(path, password)
            except PasswordError as e:
                err = f"✗ {str(e).capitalize()}"
                continue
            except Exception as e:  # noqa: BLE001
                self.win.log(f"Не удалось открыть архив: {e}")
                return
        names = ", ".join(e["profile"]["name"] for e in m["profiles"][:5]) + ("…" if len(m["profiles"]) > 5 else "")
        text = (f"Будут добавлены профили ({len(m['profiles'])}): {names}.\n"
                f"Архив создан {m.get('created', '?')}. Существующие профили не изменятся.")
        if not confirm(self.win, "Импорт профилей", text, ok_text="Импортировать", danger=False):
            return
        self.win.log(f"Импорт профилей из {path}…")

        def done(r, e):
            if e:
                self.win.log(f"Ошибка импорта: {e}")
            else:
                ids = ", ".join(f"#{i}" for i in r["profiles"])
                self.win.log(f"Импортировано профилей: {len(r['profiles'])} ({ids}), cookies: {r['cookies']}, "
                             f"аккаунтов: {r['accounts']}"
                             + (f". С другого ПК: {r['foreign']} — сохранённые в Chrome пароли не переносятся, "
                                "cookies восстановлены из архива" if r["foreign"] else ""))
            self.refresh()

        run_bg(lambda: manager.run(import_archive(manager, path, password=password), timeout=3600), done, self)

    # ---------- пароли и 2FA ----------
    @staticmethod
    def _accounts_line(pid: int):
        accs = db.list_accounts(pid)
        if not accs:
            return None
        n2fa = sum(1 for a in accs if a.totp)
        return ", ".join(a.service or a.login for a in accs[:4]) + ("…" if len(accs) > 4 else "") + \
            (f"  (2FA: {n2fa})" if n2fa else "")

    def accounts_selected(self):
        from ..accounts import accounts_modal
        ids = self._need()
        if ids:
            accounts_modal(self.win, ids[0], self.win.log)
            self.show_details()

    # ---------- импорт из других антидетект-браузеров ----------
    def import_menu(self):
        from PySide6.QtGui import QCursor
        m = QMenu(self)
        for text, fn in (("Аккаунты строками (login:pass:2fa:proxy)…", self.import_accounts),
                         ("Файл claudeAnty (.caprofile)…", self.import_profiles),
                         ("Dolphin Anty / AdsPower / таблица…", self.import_external)):
            a = QAction(icon("folder_open", C["text"], 14), text, m)
            a.triggered.connect(lambda _=False, fn=fn: fn())
            m.addAction(a)
        m.exec(QCursor.pos())

    def import_accounts(self):
        from ..profile_bulk import import_accounts
        ids = self.selected_ids()
        if import_accounts(self.win, db.get_profile(ids[0]) if len(ids) == 1 else None, manager, self.win.log):
            self.refresh()

    def import_external(self):
        from PySide6.QtWidgets import QCheckBox, QLineEdit
        from ... import importers
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        src = QComboBox()
        src.addItem("Таблица или файл (CSV, XLSX, JSON) — шаблоны AdsPower, Dolphin и свои", "file")
        src.addItem("Dolphin Anty — через API (токен из личного кабинета)", "dolphin")
        src.addItem("AdsPower — через локальный API (AdsPower должен быть запущен)", "adspower")
        path = QLineEdit()
        path.setPlaceholderText("Путь к файлу")
        browse = QPushButton("Выбрать…")
        prow = QHBoxLayout()
        prow.addWidget(path, 1)
        prow.addWidget(browse)
        browse.clicked.connect(lambda: path.setText(QFileDialog.getOpenFileName(
            self, "Файл с профилями", "", "Таблицы (*.xlsx *.csv *.txt *.json);;Все (*)")[0] or path.text()))
        token = QLineEdit()
        token.setPlaceholderText("API-токен Dolphin Anty")
        token.setEchoMode(QLineEdit.Password)
        base = QLineEdit("http://local.adspower.net:50325")
        key = QLineEdit()
        key.setPlaceholderText("API key AdsPower (если включена проверка ключа)")
        cookies = QCheckBox("Перенести cookies (если они есть в источнике)")
        cookies.setChecked(True)
        hint = QLabel("Переносятся название, ОС и тип, прокси (с ссылкой смены IP), cookies, логин/пароль/2FA "
                      "(в «Пароли и 2FA»), заметки, теги и группа. Отпечаток создаётся новый, свой: чужой "
                      "User-Agent со старой версией Chrome не совпал бы с движком. Колонки таблицы узнаются "
                      "по названиям: name, proxy (или proxytype/ip/port/user/pass), cookie, username, password, "
                      "fakey/2fa, ua/platform, remark, group, tags.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(src)
        pw = QWidget()
        pw.setLayout(prow)
        prow.setContentsMargins(0, 0, 0, 0)
        for x in (pw, token, base, key, cookies, hint):
            lay.addWidget(x)

        def sync():
            m = src.currentData()
            pw.setVisible(m == "file")
            token.setVisible(m == "dolphin")
            base.setVisible(m == "adspower")
            key.setVisible(m == "adspower")
        src.currentIndexChanged.connect(lambda _: sync())
        sync()

        def validate():
            m = src.currentData()
            if m == "file" and not Path(path.text().strip()).is_file():
                return "Выберите файл"
            if m == "dolphin" and not token.text().strip():
                return "Вставьте токен Dolphin Anty"
            return None

        if not Modal(self.win, "Импорт из других браузеров", w, ok_text="Импортировать", width=620,
                     validate=validate).exec():
            return
        mode, p_path, tok, b, k, ck = (src.currentData(), path.text().strip(), token.text().strip(), base.text().strip(),
                                       key.text().strip(), cookies.isChecked())
        say = manager.scheduler.say
        self.win.log("Импорт профилей…")

        def job():
            if mode == "file":
                rows = importers.read_file(p_path)
            elif mode == "dolphin":
                rows = importers.fetch_dolphin(tok, say)
            else:
                rows = importers.fetch_adspower(b, k, say)
            if not rows:
                raise ValueError("в источнике не найдено ни одного профиля")
            return manager.run(importers.import_items(manager, rows, say, cookies=ck), timeout=None)

        def done(r, e):
            if e:
                self.win.log(f"Ошибка импорта: {e}")
            else:
                self.win.log(f"Импортировано профилей: {len(r['profiles'])}, cookies: {r['cookies']}, "
                             f"аккаунтов: {r['accounts']}" + (f", ошибок: {len(r['errors'])}" if r["errors"] else ""))
                for err in r["errors"][:10]:
                    self.win.log("  " + err)
            self.refresh()

        run_bg(job, done, self)

    # ---------- стабильность отпечатка ----------
    def _selection_changed(self):
        n = len(self.selected_ids())
        self.bulk_bar.setVisible(n > 1)
        self.bulk_label.setText(f"Выбрано: {n}")

    def _update_tip(self, profiles, proxies):
        steps = [("Добавьте прокси — без него сайты видят ваш настоящий IP", "Добавить прокси", bool(proxies)),
                 ("Создайте профиль", "Создать профиль", bool(profiles)),
                 ("Проверьте отпечаток профиля", "Проверить", any(p.last_check for p in profiles))]
        todo = [st for st in steps if not st[2]]
        show = bool(profiles) and bool(todo) and not settings.get("onboarding_done")
        self.tip.setVisible(show)
        if show:
            marks = "   ".join(("✓ " if ok else "○ ") + t.split(" — ")[0] for t, _, ok in steps)
            self.tip_text.setText(f"Начало работы:   {marks}")
            self._tip_next = todo[0][1]
            self.tip_btn.setText(todo[0][1])

    def _tip_action(self):
        what = getattr(self, "_tip_next", "")
        if what == "Добавить прокси":
            self.onboarding._proxies()
        elif what == "Создать профиль":
            self.new_profile()
        else:
            if not self.selected_ids():
                self.table.selectRow(0)
            self.check_selected()

    def folder_selected(self):
        ids = self._need()
        if ids:
            from ..profile_bulk import move_to_folder
            if move_to_folder(self.win, ids, self.win.log):
                self.refresh()

    def tags_selected(self):
        ids = self._need()
        if ids:
            from ..profile_bulk import edit_tags
            if edit_tags(self.win, ids, self.win.log):
                self.refresh()

    def activity_selected(self):
        ids = self._need()
        if ids:
            from ..tools_view import activity_modal
            activity_modal(self.win, ids[0])

    def plan_selected(self):
        ids = self._need()
        if ids:
            from ..tools_view import plan_modal
            plan_modal(self.win, ids, self.win.log, manager)
            self.refresh()
            self.show_details()

    def arrange_windows(self):
        from ..tools_view import arrange_modal
        ids = [i for i in self.selected_ids() if manager.is_running(i)]
        arrange_modal(self.win, ids or [p.id for p in db.list_profiles() if manager.is_running(p.id)])

    def banstats(self):
        from ..tools_view import banstats_modal
        banstats_modal(self.win)

    def uniqueness_selected(self):
        from ..tools_view import uniqueness_modal
        uniqueness_modal(self.win, self.selected_ids(), self.win.log, manager)

    def health_selected(self):
        from ..health_view import health_modal
        health_modal(self.win, self.selected_ids())

    def stability_selected(self):
        from ...check import stability_test
        for pid in self._need():
            if pid in self.busy:
                continue
            if manager.is_running(pid):
                self.win.log(f"#{pid}: остановите профиль — для проверки стабильности он запускается заново")
                continue
            self.busy[pid] = "…  Стабильность"
            self.win.log(f"Стабильность отпечатка #{pid}: три запуска подряд…")

            def done(r, e, pid=pid):
                self.busy.pop(pid, None)
                if e:
                    self.win.log(f"Ошибка проверки стабильности #{pid}: {e}")
                elif r["changed"]:
                    self.win.log(f"#{pid}: отпечаток МЕНЯЕТСЯ между запусками: "
                                 + ", ".join(r["parts"][k] for k in r["changed"]))
                else:
                    self.win.log(f"#{pid}: отпечаток одинаковый во всех {r['runs']} запусках ("
                                 + ", ".join(r["parts"].values()) + ")"
                                 + (f"; не удалось снять: {', '.join(r['errors'])}" if r["errors"] else ""))
                self.refresh()

            run_bg(lambda pid=pid: manager.run(stability_test(manager, pid, 3), timeout=600), done, self)
        self.refresh()
