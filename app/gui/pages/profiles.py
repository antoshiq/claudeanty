from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
                               QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ... import cookies as ck
from ... import db, settings
from ...check import format_report, open_check_sites, run_check
from ...external_check import format_external, run_external
from ...fingerprints.generator import DEVICES
from ...manager import manager
from ...models import STATUSES, Profile, Proxy
from ...warmup import warmup
from ..icons import icon
from ..modal import Modal, ask_int, confirm
from ..profile_form import profile_modal
from ..theme import C
from ..widgets import dot_icon, empty_label, fill_list, section, set_icon, side_list, side_title, tool
from ..workers import run_bg

COLS = ["#", "Название", "Метка", "Тип", "Движок", "Прокси", "Статус", "Папка", "Теги"]
WIDTHS = (44, 0, 110, 160, 95, 160, 115, 100, 120)  # 0 = растягивается
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
                               tool("folder_open", "Импорт профилей из файла (.caprofile)", page.import_profiles),
                               tool("refresh", "Обновить (F5)", page.refresh)))
        self.folders = side_list()
        self.statuses = side_list()
        self.tags = side_list()
        for lst, kind in ((self.folders, "folder"), (self.statuses, "status"), (self.tags, "tag")):
            lst.itemClicked.connect(lambda it, k=kind: self._pick(k, it))
        v.addWidget(section("ПАПКИ"))
        v.addWidget(self.folders, 3)
        v.addWidget(section("МЕТКИ"))
        v.addWidget(self.statuses, 2)
        v.addWidget(section("ТЕГИ"))
        v.addWidget(self.tags, 2)

    def _pick(self, kind, item):
        val = item.data(Qt.UserRole)
        self.filter = None if val is None else (kind, val)
        self.page.refresh()

    def update_lists(self, profiles):
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
        hl.addWidget(self.crumbs)
        hl.addStretch()
        hl.addWidget(stop)
        hl.addWidget(start)
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
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        for keys, fn in ((Qt.Key_Return, self.start_selected), (Qt.Key_Enter, self.start_selected),
                         (Qt.Key_Delete, self.delete_selected), (Qt.Key_F2, self.edit_selected)):
            QShortcut(QKeySequence(keys), self.table, activated=fn, context=Qt.WidgetShortcut)
        v.addWidget(self.table, 1)
        self.empty = empty_label()
        v.addWidget(self.empty, 1)

        # действия редактора (справа от вкладок)
        self.tab_actions = QWidget()
        al = QHBoxLayout(self.tab_actions)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(2)
        al.addStretch()
        al.addWidget(tool("fingerprint", "Проверить отпечаток", self.check_selected))
        al.addWidget(tool("shield", "Отчёт внешних детекторов (pixelscan, CreepJS, fingerprint.com, browserleaks)",
                          self.external_selected))
        al.addWidget(tool("key", "Пароли и 2FA профиля", self.accounts_selected))
        al.addWidget(tool("flame", "Прогреть cookies", self.warmup_selected))
        al.addWidget(tool("upload", "Импорт cookies в выбранный профиль", self.import_cookies))
        al.addWidget(tool("download", "Экспорт cookies выбранного профиля", self.export_cookies))
        al.addWidget(tool("edit", "Изменить профиль (F2)", self.edit_selected))
        al.addWidget(tool("trash", "Удалить (Del)", self.delete_selected))

    # ---------- данные ----------
    def refresh(self):
        profiles = db.list_profiles()
        self.sidebar.update_lists(profiles)
        flt = self.sidebar.filter
        q = self.win.search.text().lower().strip()
        proxies = {p.id: p for p in db.list_proxies()}
        sel = set(self.selected_ids())

        def keep(p: Profile) -> bool:
            if flt and flt[0] == "folder" and p.folder != flt[1]:
                return False
            if flt and flt[0] == "tag" and flt[1] not in p.tags:
                return False
            if flt and flt[0] == "status" and p.status != flt[1]:
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
                    f"{px.host}:{px.port}  {px.country}".strip() if px else "—", state, p.folder, ", ".join(p.tags)]
            for c, val in enumerate(vals):
                item = QTableWidgetItem(val)
                if c == 0:
                    item.setForeground(QColor(C["faint"]))
                elif c == 2:
                    if p.status:
                        item.setIcon(dot_icon(st_color))
                    else:
                        item.setForeground(QColor(C["faint"]))
                elif c == 3:
                    item.setIcon(icon(kind_icon, C["muted"], 14))
                elif c == 6:
                    item.setForeground(QColor(C["accent"] if p.id in self.busy else C["green"] if on else C["faint"]))
                elif c in (5, 7, 8):
                    item.setForeground(QColor(C["muted"]))
                self.table.setItem(r, c, item)
            if p.id in sel:
                self.table.selectRow(r)
        self.table.blockSignals(False)

        self.table.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        self.empty.setText("Ничего не найдено по текущему фильтру." if profiles else
                           "Профилей пока нет.\nНажмите «+» или Ctrl+N, чтобы создать первый.")
        if not flt:
            where = "Все профили"
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
            ("webgl", gpu), ("cpu / ram", cpu), ("touch", touch), ("locale", p.locale), ("timezone", tz),
            ("webrtc", {"proxy": "только через прокси", "off": "отключён", "real": "как есть"}[p.webrtc]),
            ("proxy", f"{px.type}://{px.host}:{px.port} {px.country}" if px else "нет"),
            ("start", ", ".join(p.start_urls) or None), ("extensions", str(len(p.extensions)) if p.extensions else None),
            ("notes", p.notes.replace("\n", " ") or None),
            ("accounts", self._accounts_line(p.id)),
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
        ids = self.selected_ids()
        if not ids:
            return
        m = QMenu(self)

        def add(menu, ico, text, fn, keys=""):
            a = QAction(icon(ico, C["text"], 14) if ico else dot_icon("#00000000"), text + (f"\t{keys}" if keys else ""), menu)
            a.triggered.connect(fn)
            menu.addAction(a)
            return a

        add(m, "play", "Запустить", self.start_selected, "Enter")
        add(m, "stop", "Остановить", self.stop_selected)
        m.addSeparator()
        add(m, "edit", "Изменить…", self.edit_selected, "F2")
        add(m, "copy", "Создать копии…", self.bulk_create)
        st = m.addMenu(dot_icon(C["accent"]), "Метка")
        for key, (label, color) in STATUSES.items():
            a = QAction(dot_icon(color), label, st)
            a.triggered.connect(lambda _=False, k=key: self.set_status(k))
            st.addAction(a)
        add(m, "globe", "Назначить прокси…", self.assign_proxies)
        add(m, "key", "Пароли и 2FA…", self.accounts_selected)
        m.addSeparator()
        add(m, "fingerprint", "Проверить отпечаток", self.check_selected)
        add(m, "shield", "Отчёт внешних детекторов", self.external_selected)
        add(m, "external", "Открыть сайты проверки", self.open_sites)
        add(m, "flame", "Прогреть cookies", self.warmup_selected)
        m.addSeparator()
        add(m, "upload", "Импорт cookies…", self.import_cookies)
        add(m, "download", "Экспорт cookies…", self.export_cookies)
        m.addSeparator()
        add(m, "download", "Экспорт профилей в файл…", self.export_profiles)
        add(m, "folder_open", "Импорт профилей из файла…", self.import_profiles)
        m.addSeparator()
        add(m, "trash", "Удалить", self.delete_selected, "Del")
        m.exec(self.table.viewport().mapToGlobal(pos))

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
            manager._prepare_fingerprint(p)
            db.save_profile(p)
            self.win.log(f"Профиль #{p.id} сохранён" + (" — изменения применятся при следующем запуске"
                                                         if manager.is_running(p.id) else ""))
            self.refresh()
            self.show_details()

    def bulk_create(self):
        ids = self.selected_ids()
        tpl = db.get_profile(ids[0]) if ids else None
        label = (f"Будут созданы копии профиля «{tpl.name}» с новыми отпечатками." if tpl else
                 "Будут созданы новые профили с настройками по умолчанию.") + "\nСколько профилей создать?"
        n = ask_int(self.win, "Массовое создание", label, 5, 1, 500)
        if not n:
            return
        if not tpl:
            tpl = Profile(name="Profile", kind=settings.get("default_kind"), engine=settings.get("default_engine"),
                          os=settings.get("default_os"), device=settings.get("default_device"),
                          auto_geo=settings.get("default_auto_geo"), status="new")
        base = tpl.name.rstrip("0123456789 ") or "Profile"
        for i in range(n):
            p = tpl.model_copy(deep=True)
            p.id = None
            p.fingerprint = {}
            p.last_check = {}
            p.name = f"{base} {i + 1}"
            manager.create_profile(p)
        self.win.log(f"Создано профилей: {n}")
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
                p.status = key
                db.save_profile(p)
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
            it = QListWidgetItem(f"{ {'ok': '✓', 'fail': '✗'}.get(px.status, '—') }  {px.type}://{px.host}:{px.port}  {px.country}")
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
        for pid in self._need():
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
    def export_profiles(self):
        from PySide6.QtWidgets import QCheckBox
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
        secrets = QCheckBox("Включить пароли и 2FA-секреты")
        warn = QLabel("В файле они будут в открытом виде — храните его как пароль.")
        warn.setObjectName("hint")
        warn.setWordWrap(True)
        lay.addWidget(info)
        lay.addWidget(secrets)
        lay.addWidget(warn)
        if not Modal(self.win, "Экспорт профилей", w, ok_text="Выбрать файл…", width=520).exec():
            return
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

        run_bg(lambda: manager.run(export_profiles(manager, ids, path, include_secrets=inc), timeout=3600), done, self)

    def import_profiles(self, path: str = ""):
        from ...backup import EXT, import_archive, read_manifest
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Импорт профилей", "", f"Профили claudeAnty (*{EXT} *.zip);;Все (*)")
        if not path:
            return
        try:
            m = read_manifest(path)
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

        run_bg(lambda: manager.run(import_archive(manager, path), timeout=3600), done, self)

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
