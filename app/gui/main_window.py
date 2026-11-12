import time

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QPushButton, QSplitter,
                               QStackedWidget, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from .. import db, settings
from ..config import API_HOST, API_PORT, PROFILES_DIR
from ..manager import manager
from . import embed
from .icons import icon
from .pages.profiles import ProfilesPage
from .pages.proxies import ProxiesPage
from .pages.scenarios_page import ScenariosPage
from .pages.scheduler_page import SchedulerPage
from .pages.settings_page import SettingsPage
from .pages.sync_page import SyncPage
from .theme import C
from .tabstrip import TabStrip
from .widgets import retint, tool
from .workers import run_bg

from .. import __version__ as VERSION
GAP = 6  # промежуток между карточками


def card(name: str) -> QFrame:
    f = QFrame()
    f.setObjectName(name)
    f.setAttribute(Qt.WA_StyledBackground, True)
    return f


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("claudeAnty")
        self.resize(1380, 840)
        self.browser_tabs: dict[int, embed.BrowserTab] = {}

        root = QWidget()
        root.setObjectName("central")
        rv = QVBoxLayout(root)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        rv.addWidget(self._build_titlebar())

        # общие элементы, нужные страницам при создании
        self.output = QPlainTextEdit()
        self.output.setObjectName("console")
        self.output.setReadOnly(True)
        self.details = QPlainTextEdit()
        self.details.setObjectName("console")
        self.details.setReadOnly(True)
        self.msg = QLabel("Готово")
        self.pages = {cls.key: cls(self) for cls in (ProfilesPage, ProxiesPage, SyncPage, ScenariosPage, SchedulerPage,
                                                     SettingsPage)}

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, GAP + 2, 0)
        body.setSpacing(0)
        body.addWidget(self._build_activity())

        # --- карточка боковой панели ---
        self.side_card = card("sideCard")
        self.side_card.setMinimumWidth(190)
        sl = QVBoxLayout(self.side_card)
        sl.setContentsMargins(1, 1, 1, 6)
        self.side_stack = QStackedWidget()
        self.side_stack.setObjectName("sideStack")
        for page in self.pages.values():
            self.side_stack.addWidget(page.sidebar)
        sl.addWidget(self.side_stack)

        self.hsplit = QSplitter(Qt.Horizontal)
        self.hsplit.setHandleWidth(GAP)
        self.hsplit.addWidget(self.side_card)
        self.hsplit.addWidget(self._build_editor())
        self.hsplit.setSizes([250, 1100])
        self.hsplit.setCollapsible(1, False)
        body.addWidget(self.hsplit, 1)
        rv.addLayout(body, 1)
        self.setCentralWidget(root)

        self._build_statusbar()
        self._build_shortcuts()

        self.open_view("profiles")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.set_refresh_interval(settings.get("refresh_interval"))
        self.log(f"claudeAnty {VERSION} запущен. Локальный API: "
                 + (f"http://{API_HOST}:{API_PORT}" if settings.get("api_enabled") else "выключен"))
        # события планировщика и сценариев приходят из потока браузеров — забираем их таймером
        self._event_seq = 0
        self.event_timer = QTimer(self)
        self.event_timer.timeout.connect(self._pull_events)
        self.event_timer.start(700)
        self._engine_warned = ""
        self.update_timer = QTimer(self)
        self.update_timer.timeout.connect(self.check_updates)
        self.update_timer.start(6 * 3600 * 1000)
        QTimer.singleShot(2000, self.check_updates)
        self.self_update_timer = QTimer(self)
        self.self_update_timer.timeout.connect(self.check_self_update)
        self.self_update_timer.start(3600 * 1000)  # раз в час смотрит, прошли ли сутки с прошлой проверки
        QTimer.singleShot(8000, self.check_self_update)

    def check_self_update(self):
        from .. import selfupdate
        self._show_update_notice(selfupdate.pending_notice())
        if not selfupdate.should_check():
            return

        def done(res, err):
            if err or not res:
                return
            selfupdate.remember(res)
            if res["available"] and not selfupdate.skipped(res["latest"]):
                self.log(f"{res['message']} Настройки → О программе → «Обновить».")
                self._show_update_notice(selfupdate.pending_notice())

        run_bg(selfupdate.check, done, self)

    def _show_update_notice(self, text):
        self.st_update.setVisible(bool(text))
        if text:
            self.st_update.setText(f"⬆ {text}")

    def _pull_events(self):
        new = [(n, t) for n, t in list(manager.scheduler.events) if n > self._event_seq]
        for n, text in new:
            self._event_seq = n
            self.log(text)
        if new and self.current_key() in ("scheduler", "scenarios", "profiles"):
            self.current_page().refresh()

    def check_updates(self):
        if not settings.get("update_check"):
            return
        from ..updates import status
        run_bg(status, lambda st, err: None if err else self.engine_status(st), self)

    def engine_status(self, st: dict):
        """Версия движка в строке состояния; предупреждение в лог — один раз на каждое новое сообщение."""
        name = "Chrome" if st["engine"] == "chrome" else "CfT"
        warn = st["level"] == "warn"
        self.st_engine.setText(f"{'⚠ ' if warn else ''}{name} {st['version'].split('.')[0]}")
        self.st_engine.setToolTip(st["message"])
        if st["level"] in ("warn", "info") and st["message"] != self._engine_warned:
            self._engine_warned = st["message"]
            self.log(("Внимание: " if warn else "") + st["message"])
            if warn:
                from ..notify import notify
                notify("engine", st["message"])

    # ================= построение =================
    def _build_titlebar(self):
        bar = QFrame()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(40)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(0, 0, 10, 0)
        name = QLabel("claudeAnty")
        name.setObjectName("appName")
        name.setFixedWidth(170)
        self.search = QLineEdit()
        self.search.setObjectName("commandCenter")
        self.search_icon = self.search.addAction(icon("search", C["muted"], 14), QLineEdit.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(520)
        self.search.textChanged.connect(lambda _: self.current_page().refresh())
        right = QWidget()
        right.setFixedWidth(170)
        rl = QHBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(2)
        rl.addStretch()
        rl.addWidget(tool("plus", "Новый профиль (Ctrl+N)", self.new_profile))
        rl.addWidget(tool("settings", "Настройки (Ctrl+,)", lambda: self.open_view("settings")))
        lay.addWidget(name)
        lay.addStretch()
        lay.addWidget(self.search)
        lay.addStretch()
        lay.addWidget(right)
        return bar

    def _build_activity(self):
        bar = QFrame()
        bar.setObjectName("activityBar")
        bar.setFixedWidth(48)
        v = QVBoxLayout(bar)
        v.setContentsMargins(0, 0, 0, 4)
        v.setSpacing(0)
        self.act_buttons: dict[str, QToolButton] = {}

        def act(key, name, tip, fn):
            b = QToolButton()
            b.setObjectName("activityBtn")
            b.setFixedSize(48, 48)
            b.setIconSize(QSize(24, 24))
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setProperty("act_icon", name)
            b.clicked.connect(fn)
            if key:
                self.act_buttons[key] = b
            return b

        v.addWidget(act("profiles", "users", "Профили (Ctrl+1)", lambda: self._activity_clicked("profiles")))
        v.addWidget(act("proxies", "globe", "Прокси (Ctrl+2)", lambda: self._activity_clicked("proxies")))
        v.addWidget(act("sync", "repeat", "Синхронизатор (Ctrl+3)", lambda: self._activity_clicked("sync")))
        v.addWidget(act("scenarios", "workflow", "Сценарии (Ctrl+4)", lambda: self._activity_clicked("scenarios")))
        v.addWidget(act("scheduler", "clock", "Планировщик (Ctrl+5)", lambda: self._activity_clicked("scheduler")))
        v.addStretch()
        self.api_btn = act(None, "plug", "Локальный API — показать адреса", self.api_info)
        v.addWidget(self.api_btn)
        v.addWidget(act("settings", "settings", "Настройки (Ctrl+,)", lambda: self._activity_clicked("settings")))
        return bar

    def _build_editor(self):
        # --- карточка вкладок ---
        self.editor_card = card("editorCard")
        v = QVBoxLayout(self.editor_card)
        v.setContentsMargins(1, 1, 1, 4)
        v.setSpacing(0)

        row = QFrame()
        row.setObjectName("tabRow")
        row.setFixedHeight(38)
        rl = QHBoxLayout(row)
        rl.setContentsMargins(6, 5, 8, 0)
        rl.setSpacing(2)
        self.tabs = TabStrip()
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        rl.addWidget(self.tabs, 0, Qt.AlignBottom)
        rl.addStretch()
        self.actions_stack = QStackedWidget()
        self.actions_stack.setObjectName("tabActions")
        self.actions_index: dict[str, int] = {}
        for key, page in self.pages.items():
            self.actions_index[key] = self.actions_stack.addWidget(getattr(page, "tab_actions", None) or QWidget())
        self.browser_actions = QWidget()
        bl = QHBoxLayout(self.browser_actions)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(2)
        bl.addStretch()
        bl.addWidget(tool("external", "Открепить в отдельное окно", self.detach_current))
        bl.addWidget(tool("stop", "Остановить профиль", self.stop_current_browser, 14))
        self.actions_index["browser"] = self.actions_stack.addWidget(self.browser_actions)
        rl.addWidget(self.actions_stack, 0, Qt.AlignVCenter)
        v.addWidget(row)

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("pages")
        for page in self.pages.values():
            page.setObjectName("page")
            self.page_stack.addWidget(page)
        v.addWidget(self.page_stack, 1)

        # --- карточка нижней панели ---
        self.panel_card = card("panelCard")
        pl = QVBoxLayout(self.panel_card)
        pl.setContentsMargins(8, 1, 1, 4)
        self.panel = QTabWidget()
        self.panel.setObjectName("bottomPanel")
        self.panel.setDocumentMode(True)
        self.panel.tabBar().setDrawBase(False)
        self.panel.addTab(self.details, "ОТПЕЧАТОК")
        self.panel.addTab(self.output, "ВЫВОД")
        pl.addWidget(self.panel)

        self.vsplit = QSplitter(Qt.Vertical)
        self.vsplit.setHandleWidth(GAP)
        self.vsplit.addWidget(self.editor_card)
        self.vsplit.addWidget(self.panel_card)
        self.vsplit.setSizes([580, 200])
        self.vsplit.setCollapsible(0, False)
        return self.vsplit

    def _build_statusbar(self):
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        remote = QLabel(f"⇄  API :{API_PORT}" if settings.get("api_enabled") else "⇄  API выкл.")
        remote.setObjectName("remote")
        self.st_running = QLabel()
        self.st_count = QLabel()
        sb.addWidget(remote)
        sb.addWidget(self.st_running)
        sb.addWidget(self.msg, 1)
        self.st_engine = QLabel()
        sb.addPermanentWidget(self.st_engine)
        sb.addPermanentWidget(self.st_count)
        self.st_update = QPushButton()
        self.st_update.setObjectName("statusLink")
        self.st_update.setFlat(True)
        self.st_update.setCursor(Qt.PointingHandCursor)
        self.st_update.setToolTip("Открыть Настройки → О программе")
        self.st_update.clicked.connect(lambda: (self.open_view("settings"), self.pages["settings"].scroll_to("about")))
        self.st_update.setVisible(False)
        sb.addPermanentWidget(self.st_update)
        sb.addPermanentWidget(QLabel(f"claudeAnty {VERSION}"))

    def _build_shortcuts(self):
        root = self.centralWidget()
        for keys, fn in (("Ctrl+N", self.new_profile), ("Ctrl+P", self._focus_search), ("Ctrl+F", self._focus_search),
                         ("F5", self.refresh), ("Ctrl+B", self.toggle_sidebar), ("Ctrl+J", self.toggle_panel),
                         ("Ctrl+,", lambda: self.open_view("settings")), ("Ctrl+W", self._close_current),
                         ("Ctrl+Tab", lambda: self._cycle(1)), ("Ctrl+Shift+Tab", lambda: self._cycle(-1)),
                         ("Ctrl+1", lambda: self.open_view("profiles")), ("Ctrl+2", lambda: self.open_view("proxies")),
                         ("Ctrl+3", lambda: self.open_view("sync")), ("Ctrl+4", lambda: self.open_view("scenarios")),
                         ("Ctrl+5", lambda: self.open_view("scheduler"))):
            QShortcut(QKeySequence(keys), root, activated=fn, context=Qt.WidgetWithChildrenShortcut)

    # ================= вкладки =================
    def _add_tab(self, key: str, ico: str, title: str, closable: bool = True) -> int:
        return self.tabs.addTab(key, ico, title, closable)

    def current_key(self) -> str:
        i = self.tabs.currentIndex()
        return self.tabs.tabData(i) if i >= 0 else "profiles"

    def current_page(self):
        return self.pages.get(self.current_key(), self.pages["profiles"])

    def _tab_index(self, key: str) -> int:
        for i in range(self.tabs.count()):
            if self.tabs.tabData(i) == key:
                return i
        return -1

    def open_view(self, key: str):
        i = self._tab_index(key)
        if i < 0:
            page = self.pages[key]
            i = self._add_tab(key, page.icon_name, page.title, closable=key != "profiles")
        if self.tabs.currentIndex() == i:
            self._tab_changed(i)
        self.tabs.setCurrentIndex(i)

    def _activity_clicked(self, key: str):
        # повторный клик по активному разделу скрывает/показывает боковую панель, как в VS Code
        if self._activity_key() == key and self.current_key() == key:
            self.toggle_sidebar()
        else:
            self.side_card.setVisible(True)
            self.open_view(key)
        self._paint_activity()

    def _activity_key(self) -> str:
        key = self.current_key()
        return "profiles" if key.startswith("browser:") else key

    def _tab_changed(self, i: int):
        key = self.tabs.tabData(i) if i >= 0 else None
        if key is None:
            return
        if key.startswith("browser:"):
            tab = self.browser_tabs.get(int(key.split(":")[1]))
            if not tab:
                return
            self.page_stack.setCurrentWidget(tab)
            self.side_stack.setCurrentWidget(self.pages["profiles"].sidebar)
            self.actions_stack.setCurrentIndex(self.actions_index["browser"])
            self.search.setPlaceholderText("Поиск профилей   Ctrl+P")
            self._paint_activity()
            tab.container.setFocus()
            return
        if key not in self.pages:
            return
        page = self.pages[key]
        self.page_stack.setCurrentWidget(page)
        self.side_stack.setCurrentWidget(page.sidebar)
        self.actions_stack.setCurrentIndex(self.actions_index[key])
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.search.setPlaceholderText(f"{page.search_hint}   Ctrl+P")
        self._paint_activity()
        self.refresh()
        if key == "profiles":
            page.show_details()
            self.panel.setCurrentWidget(self.details)
        else:
            self.panel.setCurrentWidget(self.output)

    def _close_tab(self, i: int):
        key = self.tabs.tabData(i)
        if key == "profiles":
            return
        if key and key.startswith("browser:"):
            pid = int(key.split(":")[1])
            self._remove_browser_tab(pid)
            run_bg(lambda: manager.run(manager.stop(pid)),
                   lambda r, e: (self.log(f"Профиль #{pid} остановлен"), self.refresh()), self)
            return
        self.tabs.removeTab(i)

    def _close_current(self):
        self._close_tab(self.tabs.currentIndex())

    def _cycle(self, step: int):
        n = self.tabs.count()
        if n:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % n)

    def _paint_activity(self):
        active = self._activity_key()
        visible = self.side_card.isVisible() if hasattr(self, "side_card") else True
        for key, b in self.act_buttons.items():
            on = key == active
            b.setChecked(on)
            color = C["bright"] if on and visible else (C["text"] if on else C["faint"])
            b.setIcon(icon(b.property("act_icon"), color, 24))
        self.api_btn.setChecked(False)
        self.api_btn.setIcon(icon("plug", C["faint"], 24))

    # ================= встроенный браузер =================
    def embed_profile(self, pid: int):
        """Найти окно запущенного профиля и встроить его во вкладку."""
        if pid in self.browser_tabs:
            self.tabs.setCurrentIndex(self._tab_index(f"browser:{pid}"))
            return
        if not embed.SUPPORTED:
            return
        p = db.get_profile(pid)
        if not p:
            return
        self.log(f"Встраивание окна профиля #{pid} во вкладку…")

        def done(hwnd, err):
            if err or not hwnd:
                self.log(f"Не удалось встроить окно профиля #{pid} — браузер остался отдельным окном")
                return
            if not manager.is_running(pid) or pid in self.browser_tabs:
                return
            tab = embed.BrowserTab(p, hwnd)
            self.browser_tabs[pid] = tab
            self.page_stack.addWidget(tab)
            i = self._add_tab(f"browser:{pid}", "phone" if p.kind == "mobile" else "monitor", p.name)
            self.tabs.setCurrentIndex(i)
            self.log(f"Профиль #{pid} открыт во вкладке")

        run_bg(lambda: embed.wait_window(str(PROFILES_DIR / str(pid))), done, self)

    def _remove_browser_tab(self, pid: int):
        tab = self.browser_tabs.pop(pid, None)
        i = self._tab_index(f"browser:{pid}")
        if i >= 0:
            self.tabs.removeTab(i)
        if tab:
            self.page_stack.removeWidget(tab)
            tab.deleteLater()

    def _current_browser(self):
        key = self.current_key()
        return self.browser_tabs.get(int(key.split(":")[1])) if key.startswith("browser:") else None

    def detach_current(self):
        tab = self._current_browser()
        if tab:
            tab.detach()
            self._remove_browser_tab(tab.profile_id)
            self.log(f"Профиль #{tab.profile_id} откреплён в отдельное окно")

    def stop_current_browser(self):
        tab = self._current_browser()
        if tab:
            self._close_tab(self.tabs.currentIndex())

    def _sync_browser_tabs(self):
        # браузер закрыли изнутри (крестик Chrome) — убираем вкладку
        for pid, tab in list(self.browser_tabs.items()):
            if not manager.is_running(pid) or not tab.alive():
                self._remove_browser_tab(pid)

    # ================= общее =================
    def log(self, text: str, error: bool = False):
        self.output.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {text}")
        self.msg.setText(text)
        if error or text.lower().startswith(("ошибка", "не удалось")):
            # ошибку не прячем за вкладкой «Отпечаток»: показываем вывод и подсвечиваем строку состояния
            self.panel_card.setVisible(True)
            self.panel.setCurrentWidget(self.output)
            self.msg.setStyleSheet(f"color: {C['red']};")
            QTimer.singleShot(8000, lambda: self.msg.setStyleSheet(""))

    def set_details(self, text: str):
        self.details.setPlainText(text)

    def set_refresh_interval(self, seconds):
        self.timer.start(max(1, int(seconds)) * 1000)

    def refresh(self):
        self._sync_browser_tabs()
        self.current_page().refresh()
        n_run = len(manager.sessions)
        self.st_running.setText(f"▶ запущено: {n_run}" if n_run else "○ нет запущенных")
        self.st_count.setText(f"Профилей: {len(db.list_profiles())}   Прокси: {len(db.list_proxies())}")

    def restyle(self):
        """После смены темы или акцента перекрасить иконки и таблицы."""
        retint(self)  # в том числе иконки вкладок — они в акцентном цвете
        self._paint_activity()
        self.search_icon.setIcon(icon("search", C["muted"], 14))
        for page in self.pages.values():
            page.refresh()

    def _focus_search(self):
        self.search.setFocus()
        self.search.selectAll()

    def toggle_sidebar(self):
        self.side_card.setVisible(not self.side_card.isVisible())
        self._paint_activity()

    def toggle_panel(self):
        self.panel_card.setVisible(not self.panel_card.isVisible())

    def new_profile(self):
        self.open_view("profiles")
        self.pages["profiles"].new_profile()

    def api_info(self):
        self.api_btn.setChecked(False)
        self.panel_card.setVisible(True)
        self.panel.setCurrentWidget(self.output)
        if not settings.get("api_enabled"):
            self.log("Локальный API выключен. Включите его в Настройках и перезапустите приложение.")
            return
        base = f"http://{API_HOST}:{API_PORT}"
        auth = ("  Ключ задан (Настройки → Локальный API): добавляйте заголовок X-API-Key или ?token=\n"
                if settings.get("api_token") else "  Без ключа — задать можно в Настройках → Локальный API\n")
        self.output.appendPlainText(
            f"\nЛокальный API: {base}\n" + auth +
            f"  GET  /profiles                 список профилей\n"
            f"  GET  /profiles/{{id}}/start      запуск → ws_endpoint (Playwright / Puppeteer)\n"
            f"  GET  /profiles/{{id}}/stop       остановка\n"
            f"  GET  /profiles/{{id}}/cookies    экспорт cookies\n"
            f"  Документация: {base}/docs\n")

    def closeEvent(self, e):
        if settings.get("stop_on_exit"):
            try:
                manager.run(manager.stop_all(), timeout=30)
            except Exception:  # noqa: BLE001
                pass
        else:
            for tab in list(self.browser_tabs.values()):
                tab.detach()  # браузеры продолжают работать отдельными окнами
        super().closeEvent(e)
