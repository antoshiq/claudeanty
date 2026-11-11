import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from ... import settings
from ...config import API_PORT, DATA_DIR
from ...fingerprints.generator import DEVICES
from ..icons import icon
from ..embed import SUPPORTED as EMBED_SUPPORTED
from ..theme import ACCENTS, THEMES, C, apply_theme
from ..widgets import fill_list, set_icon, side_list, side_title


def _dot(color: str) -> QIcon:
    pm = QPixmap(14, 14)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(1, 1, 12, 12)
    p.end()
    return QIcon(pm)


class SettingsSidebar(QFrame):
    def __init__(self, page: "SettingsPage"):
        super().__init__()
        self.setObjectName("sideBar")
        self.page = page
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(side_title("НАСТРОЙКИ"))
        self.toc = side_list()
        self.toc.itemClicked.connect(lambda it: page.scroll_to(it.data(Qt.UserRole)))
        v.addWidget(self.toc, 1)

    def update_lists(self, sections):
        fill_list(self.toc, [(title, key, ico) for key, title, ico in sections], "\0")


class SettingsPage(QWidget):
    key, title, icon_name = "settings", "Настройки", "settings"
    search_hint = "Поиск настроек"

    SECTIONS = [("engine", "Движок", "shield"), ("profiles", "Новые профили", "users"), ("browser", "Поведение", "monitor"),
                ("proxies", "Прокси", "globe"), ("warmup", "Прогрев cookies", "flame"), ("rpa", "Сценарии", "workflow"),
                ("ui", "Внешний вид", "layers"),
                ("api", "Локальный API", "plug"), ("notify", "Уведомления", "bell"), ("data", "Данные", "folder")]

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.sidebar = SettingsSidebar(self)
        self.rows: list[tuple[QFrame, str]] = []
        self.headers: dict[str, QLabel] = {}

        self.scroll = QScrollArea()
        self.scroll.setObjectName("settingsScroll")
        self.scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("settingsBody")
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(40, 26, 40, 40)
        self.body.setSpacing(4)
        self.scroll.setWidget(body)
        self.scroll.viewport().setAutoFillBackground(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.scroll)

        h1 = QLabel("Настройки")
        h1.setObjectName("settingsH1")
        self.body.addWidget(h1)
        note = QLabel("Изменения сохраняются автоматически.")
        note.setObjectName("settingDesc")
        self.body.addWidget(note)

        # ---------- движок ----------
        self._h("engine")
        from ...engines.chrome_version import find_chrome
        chrome = find_chrome()
        self._combo("engine", "Браузер для Chromium-профилей",
                          "Google Chrome — установленный браузер: сам обновляется, есть Widevine (DRM) и все кодеки, "
                          "как у обычных людей (рекомендуется). Chrome for Testing — сборка Playwright, без Widevine. "
                          "Применяется к следующим запускам профилей.", "chrome_engine",
                          [("Google Chrome (установленный)" if chrome else "Google Chrome — не установлен", "chrome"),
                           ("Chrome for Testing (Playwright)", "chromium")], on_change=lambda _: self.check_engine())
        try:
            import patchright  # noqa: F401
            have_pr = True
        except ImportError:
            have_pr = False
        self._combo("engine", "Управление браузером",
                    "Patchright — форк Playwright без следов автоматизации: не включает на страницах CDP-домены "
                    "Runtime и Console, по которым сайты (fingerprint.com) видят «открытые DevTools». Playwright — "
                    "обычный, запасной вариант. Применяется к следующим запускам профилей.", "automation_driver",
                    [("Patchright (рекомендуется)" if have_pr else "Patchright — не установлен (pip install patchright)",
                      "patchright"), ("Playwright", "playwright")])
        self.engine_state = QLabel("Проверка версии…")
        self.engine_state.setObjectName("settingDesc")
        self.engine_state.setWordWrap(True)
        self.engine_state.setMinimumWidth(460)
        btns = QWidget()
        bl = QHBoxLayout(btns)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)
        chk_btn = QPushButton("  Проверить обновления")
        set_icon(chk_btn, "refresh", "text", 14)
        chk_btn.clicked.connect(lambda: self.check_engine(force=True))
        self.update_btn = QPushButton("  Обновить движок")
        set_icon(self.update_btn, "download", "text", 14)
        self.update_btn.clicked.connect(self.update_engine)
        for b in (chk_btn, self.update_btn):
            b.setCursor(Qt.PointingHandCursor)
            bl.addWidget(b)
        bl.addStretch()
        box = QWidget()
        bv = QVBoxLayout(box)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.addWidget(self.engine_state)
        bv.addWidget(btns)
        self._row("engine", "Версия", "Устаревшая версия Chrome в User-Agent выделяется на фоне обычных пользователей.", box)
        self._check("engine", "Следить за обновлениями",
                    "При запуске и раз в 6 часов сверять версию движка с актуальной стабильной и предупреждать, "
                    "если он отстаёт.", "update_check")
        self._engine_busy = False
        QTimer.singleShot(300, self.check_engine)

        # ---------- новые профили ----------
        self._h("profiles")
        self._combo("profiles", "Тип по умолчанию", "Какой профиль создаётся по кнопке «+».", "default_kind",
                    [("Компьютер", "desktop"), ("Телефон (Android)", "mobile")])
        self._combo("profiles", "Движок по умолчанию", "Для профилей-компьютеров. Телефоны всегда используют Chromium.",
                    "default_engine", [("Chromium", "chromium"), ("Camoufox (Firefox)", "camoufox")])
        self._combo("profiles", "ОС по умолчанию", "Операционная система в отпечатке нового ПК-профиля.", "default_os",
                    [("Windows", "windows"), ("macOS", "macos"), ("Linux", "linux")])
        self._combo("profiles", "Телефон по умолчанию", "Модель устройства для новых мобильных профилей.",
                    "default_device", [("Случайное устройство", "")] + [(v["label"], k) for k, v in DEVICES.items()])
        self._check("profiles", "Гео по IP прокси", "Подставлять таймзону, язык и геолокацию из IP прокси при запуске.",
                    "default_auto_geo")

        # ---------- поведение ----------
        self._h("browser")
        self._check("browser", "Закрывать браузеры при выходе",
                    "Останавливать все запущенные профили, когда закрывается claudeAnty.", "stop_on_exit")
        emb = self._check("browser", "Браузер во вкладке приложения",
                          "Открывать окно браузера внутри claudeAnty отдельной вкладкой, а не отдельным окном. "
                          "Отпечатки не меняются — это тот же браузер. Вкладку можно открепить в отдельное окно.",
                          "embed_browser")
        if not EMBED_SUPPORTED:
            emb.setEnabled(False)
            emb.setText("Недоступно: работает только в Windows")
        self._check("browser", "Подтверждать удаление", "Спрашивать перед удалением профилей и прокси.", "confirm_delete")

        # ---------- прокси ----------
        self._h("proxies")
        self._check("proxies", "Проверять после добавления",
                    "Сразу проверять новые прокси: страна, таймзона, тип IP (резидентный, мобильный, хостинг) "
                    "и ОС по TCP/IP-отпечатку.", "check_proxy_on_add")
        self._check("proxies", "Менять IP мобильного прокси перед запуском",
                    "Если у прокси задана ссылка смены IP (меню прокси → «Ссылка смены IP…»), она открывается перед "
                    "запуском профиля. Не срабатывает, если прокси занят другим запущенным профилем.",
                    "change_ip_on_start")
        self._spin("proxies", "Смена IP не чаще, сек", "Пауза между сменами IP одного прокси (лимит провайдера).",
                   "change_ip_cooldown", 0, 3600)
        self._spin("proxies", "Ждать новый IP, сек", "Сколько ждать, пока модем переподключится.",
                   "change_ip_wait", 5, 300)

        # ---------- прогрев ----------
        self._h("warmup")
        self._spin("warmup", "Сайтов за прогрев", "Сколько случайных сайтов из списка обходит профиль за один прогрев.",
                   "warmup_count", 1, 50)
        self._spin("warmup", "Время на сайте", "Примерно сколько секунд профиль проводит на каждом сайте: "
                   "двигает мышью и прокручивает страницу.", "warmup_dwell", 3, 120)
        sites = QPlainTextEdit()
        sites.setMinimumWidth(460)
        sites.setFixedHeight(140)
        sites.setPlaceholderText("Пусто — встроенный список из 20 популярных сайтов\nhttps://www.google.com\n"
                                 "https://www.youtube.com")
        sites.setPlainText("\n".join(settings.get("warmup_sites") or []))
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(600)
        timer.timeout.connect(lambda: self._saved("warmup_sites",
                                                  [u.strip() for u in sites.toPlainText().splitlines() if u.strip()]))
        sites.textChanged.connect(timer.start)
        self._row("warmup", "Список сайтов", "По одному адресу в строке. Прогрев запускается из меню профиля "
                  "или кнопкой с огоньком над таблицей; метка «Новый» меняется на «Готов».", sites)

        # ---------- сценарии ----------
        self._h("rpa")
        self._spin("rpa", "Профилей одновременно", "Сколько профилей сценарий выполняет параллельно; остальные ждут "
                   "очереди. Касается и запуска по расписанию.", "rpa_parallel", 1, 50)
        self._combo("rpa", "Сервис капч", "Для шага «Решить капчу»: reCAPTCHA v2, hCaptcha, Cloudflare Turnstile.",
                    "captcha_provider", [("2captcha", "2captcha"), ("CapSolver", "capsolver")])
        self._secret("rpa", "Ключ сервиса капч", "API-ключ из личного кабинета 2captcha / CapSolver.", "captcha_key",
                     check=("Проверить баланс", self._captcha_balance))

        # ---------- внешний вид ----------
        self._h("ui")
        self._combo("ui", "Тема", "Оформление интерфейса. OLED-темы используют чистый чёрный или белый фон.", "theme",
                    [(label, key) for key, (label, _) in THEMES.items()], on_change=self._accent_changed)
        accent = self._combo("ui", "Акцентный цвет", "Цвет кнопок, выделения и активных вкладок.", "accent",
                             [(label, key) for key, (label, _) in ACCENTS.items()], on_change=self._accent_changed)
        for i, (key, (_, color)) in enumerate(ACCENTS.items()):
            accent.setItemIcon(i, _dot(color))
        self._spin("ui", "Интервал обновления", "Как часто обновлять таблицы и статусы, в секундах.",
                   "refresh_interval", 1, 30, on_change=lambda v: self.win.set_refresh_interval(v))

        # ---------- API ----------
        self._h("api")
        self._check("api", "Включить локальный API",
                    "HTTP API на 127.0.0.1 для Playwright, Puppeteer и Selenium. Применится после перезапуска.",
                    "api_enabled")
        self._spin("api", "Порт", f"Сейчас API работает на порту {API_PORT}. Новый порт применится после перезапуска.",
                   "api_port", 1024, 65535)

        # ---------- уведомления ----------
        self._h("notify")
        self._secret("notify", "Токен бота Telegram", "Создайте бота у @BotFather и вставьте его токен. Затем напишите "
                     "боту /start и нажмите «Определить chat id».", "tg_token",
                     check=("Определить chat id", self._tg_detect))
        self.tg_chat = self._secret("notify", "Chat id", "Куда отправлять: ваш чат с ботом, группа или канал "
                                    "(бот должен быть их участником).", "tg_chat", check=("Отправить тест", self._tg_test))
        from PySide6.QtWidgets import QCheckBox as _CB
        from ...notify import EVENTS
        ev_box = QWidget()
        evl = QVBoxLayout(ev_box)
        evl.setContentsMargins(0, 0, 0, 0)
        evl.setSpacing(4)
        chosen = set(settings.get("tg_events") or [])
        self._tg_checks = {}
        for k, title in EVENTS.items():
            c = _CB(title)
            c.setChecked(k in chosen)
            c.toggled.connect(lambda _=False: self._saved("tg_events", [k for k, c in self._tg_checks.items() if c.isChecked()]))
            self._tg_checks[k] = c
            evl.addWidget(c)
        self._row("notify", "О чём сообщать", "Уведомления приходят, пока открыт claudeAnty.", ev_box)

        # ---------- данные ----------
        self._h("data")
        btn = QPushButton("  Открыть папку данных")
        set_icon(btn, "folder_open", "text", 14)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: os.startfile(str(DATA_DIR)))
        self._row("data", "Папка данных", f"{DATA_DIR}\nЗдесь хранятся база профилей, прокси, настройки и данные браузеров.", btn)
        bk = QWidget()
        bkl = QHBoxLayout(bk)
        bkl.setContentsMargins(0, 0, 0, 0)
        bkl.setSpacing(6)
        for text, ico, fn in (("  Создать сейчас", "download", self.backup_now),
                              ("  Восстановить…", "upload", self.restore_backup),
                              ("  Папка копий", "folder_open", self.open_backups)):
            b = QPushButton(text)
            set_icon(b, ico, "text", 14)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            bkl.addWidget(b)
        bkl.addStretch()
        self._row("data", "Резервная копия", "Все профили (с cookies и данными браузера) и прокси в один файл в "
                  "data/backups. По расписанию — задача «Резервная копия» в Планировщике. «Восстановить» добавляет "
                  "профили из копии, существующие не меняются.", bk)
        self._spin("data", "Хранить копий", "Сколько последних резервных копий оставлять; более старые удаляются.",
                   "backup_keep", 1, 100)
        self._check("data", "Пароли и 2FA в резервных копиях",
                    "В базе они зашифрованы ключом Windows этого ПК и на другом компьютере не расшифруются. "
                    "Если включить, в копию они попадут в открытом виде.", "backup_secrets")

        self.body.addStretch()
        self.sidebar.update_lists(self.SECTIONS)

    # ---------- построение строк ----------
    def _h(self, key: str):
        title = dict((k, t) for k, t, _ in self.SECTIONS)[key]
        h = QLabel(title)
        h.setObjectName("settingsH2")
        h.setContentsMargins(0, 22, 0, 6)
        self.headers[key] = h
        self.body.addWidget(h)

    def _row(self, sec: str, title: str, desc: str, control: QWidget) -> QFrame:
        row = QFrame()
        row.setObjectName("settingRow")
        v = QVBoxLayout(row)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(4)
        cat = dict((k, t) for k, t, _ in self.SECTIONS)[sec]
        t = QWidget()
        tl = QHBoxLayout(t)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(0)
        c1 = QLabel(f"{cat}: ")
        c1.setObjectName("settingCat")
        c2 = QLabel(title)
        c2.setObjectName("settingTitle")
        tl.addWidget(c1)
        tl.addWidget(c2)
        tl.addStretch()
        d = QLabel(desc)
        d.setObjectName("settingDesc")
        d.setWordWrap(True)
        v.addWidget(t)
        v.addWidget(d)
        if not isinstance(control, QCheckBox):
            box = QWidget()
            bl = QHBoxLayout(box)
            bl.setContentsMargins(0, 4, 0, 0)
            bl.addWidget(control)
            bl.addStretch()
            v.addWidget(box)
        self.body.addWidget(row)
        self.rows.append((row, f"{cat} {title} {desc}".lower()))
        return row

    def _saved(self, key, value, on_change=None):
        settings.set(key, value)
        self.win.log(f"Настройка сохранена: {key} = {value}")
        if on_change:
            on_change(value)

    def _combo(self, sec, title, desc, key, options, on_change=None) -> QComboBox:
        cb = QComboBox()
        cb.setMinimumWidth(280)
        for label, val in options:
            cb.addItem(label, val)
        cb.setCurrentIndex(max(0, cb.findData(settings.get(key))))
        cb.currentIndexChanged.connect(lambda _: self._saved(key, cb.currentData(), on_change))
        self._row(sec, title, desc, cb)
        return cb

    def _check(self, sec, title, desc, key) -> QCheckBox:
        chk = QCheckBox("Включено")
        chk.setChecked(bool(settings.get(key)))
        chk.toggled.connect(lambda v: self._saved(key, v))
        row = self._row(sec, title, desc, chk)
        row.layout().addWidget(chk)
        return chk

    def _spin(self, sec, title, desc, key, lo, hi, on_change=None) -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setMinimumWidth(140)
        sp.setValue(int(settings.get(key)))
        sp.valueChanged.connect(lambda v: self._saved(key, v, on_change))
        self._row(sec, title, desc, sp)
        return sp

    def _secret(self, sec, title, desc, key, check=None):
        from PySide6.QtWidgets import QLineEdit
        box = QWidget()
        bl = QHBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)
        ed = QLineEdit(settings.get(key) or "")
        ed.setEchoMode(QLineEdit.Password)
        ed.setMinimumWidth(360)
        ed.editingFinished.connect(lambda: settings.get(key) != ed.text().strip() and self._saved(key, ed.text().strip()))
        bl.addWidget(ed)
        if check:
            b = QPushButton(check[0])
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda: (self._saved(key, ed.text().strip()) if settings.get(key) != ed.text().strip() else None,
                                       check[1]()))
            bl.addWidget(b)
        self._row(sec, title, desc, box)
        return ed

    def _tg_detect(self):
        from ...notify import detect_chat
        from ..workers import run_bg

        def done(chat, e):
            if e:
                self.win.log(f"Telegram: {e}")
                return
            self.tg_chat.setText(chat)
            self._saved("tg_chat", chat)

        run_bg(lambda: detect_chat(settings.get("tg_token")), done, self)

    def _tg_test(self):
        from ...notify import send_test
        from ..workers import run_bg
        run_bg(send_test, lambda r, e: self.win.log(f"Telegram: {e or r}"), self)

    def _captcha_balance(self):
        from ...captcha import balance
        from ...manager import manager
        from ..workers import run_bg
        run_bg(lambda: manager.run(balance()), lambda r, e: self.win.log(
            f"Сервис капч: ошибка — {e}" if e else f"Баланс {settings.get('captcha_provider')}: {r:.2f} $"), self)

    def _accent_changed(self, _value):
        apply_theme(QApplication.instance())
        self.win.restyle()

    # ---------- движок ----------
    def check_engine(self, force: bool = False):
        from ...updates import status
        from ..workers import run_bg
        self.engine_state.setText("Проверка версии…")

        def done(st, err):
            if err:
                self.engine_state.setText(f"Ошибка проверки: {err}")
                return
            self.show_engine_status(st)
            self.win.engine_status(st)

        run_bg(lambda: status(force), done, self)

    def show_engine_status(self, st: dict):
        mark = {"ok": "✓", "info": "ℹ", "warn": "⚠", "error": "✗"}[st["level"]]
        self.engine_state.setText(f"{mark}  {st['message']}")
        self.update_btn.setText("  Открыть «О браузере Chrome»" if st["engine"] == "chrome" else "  Обновить движок")

    def update_engine(self):
        from ...engines.chrome_version import engine_kind
        from ... import updates
        from ..workers import run_bg
        if engine_kind() == "chrome":
            if updates.open_chrome_update():
                self.win.log("Открыта страница «О браузере Chrome» — Chrome проверит и скачает обновление. "
                             "Оно применится после закрытия всех окон Chrome.")
            return
        if self._engine_busy:
            return
        self._engine_busy = True
        self.update_btn.setEnabled(False)
        self.engine_state.setText("Обновление Chrome for Testing… это может занять несколько минут")
        self.win.log("Обновление движка Chrome for Testing…")

        def done(msg, err):
            self._engine_busy = False
            self.update_btn.setEnabled(True)
            self.win.log(f"Ошибка обновления движка: {err}" if err else msg)
            self.check_engine(force=True)

        run_bg(lambda: updates.update_chromium(), done, self)

    # ---------- резервные копии ----------
    def backup_now(self):
        from ...backup import backup_all
        from ...manager import manager
        from ..workers import run_bg
        self.win.log("Резервная копия всех профилей…")

        def done(r, e):
            self.win.log(f"Ошибка резервной копии: {e}" if e else
                         f"Резервная копия готова: профилей {r['profiles']}, {r['size'] / 1e6:.1f} МБ → {r['path']}")

        run_bg(lambda: manager.run(backup_all(manager), timeout=7200), done, self)

    def restore_backup(self):
        from PySide6.QtWidgets import QFileDialog
        from ...backup import BACKUP_DIR
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(self, "Восстановить из копии", str(BACKUP_DIR),
                                              "Профили claudeAnty (*.caprofile *.zip);;Все (*)")
        if path:
            self.win.open_view("profiles")
            self.win.pages["profiles"].import_profiles(path)

    def open_backups(self):
        from ...backup import BACKUP_DIR
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(BACKUP_DIR))

    # ---------- API страницы ----------
    def scroll_to(self, key: str):
        h = self.headers.get(key)
        if h:
            self.scroll.verticalScrollBar().setValue(h.y())

    def refresh(self):
        q = self.win.search.text().lower().strip()
        for row, text in self.rows:
            row.setVisible(not q or q in text)
