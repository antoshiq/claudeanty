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
                ("health", "Здоровье и бан", "activity"),
                ("proxies", "Прокси", "globe"), ("warmup", "Прогрев cookies", "flame"), ("rpa", "Сценарии", "workflow"),
                ("ui", "Внешний вид", "layers"),
                ("api", "Локальный API", "plug"), ("notify", "Уведомления", "bell"), ("data", "Данные", "folder"),
                ("about", "О программе", "download")]

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
        self._check("engine", "Автопроверка после обновления",
                    "Когда версия Chrome сменилась (Chrome обновился сам или нажата «Обновить движок»), профили-образцы "
                    "без окна проходят встроенную проверку и проверку уникальности; результат и сравнение с прошлой "
                    "проверкой — в Telegram (событие «Chrome обновился»). Работает, пока открыт claudeAnty.",
                    "engine_watch")
        wbox = QWidget()
        wv = QVBoxLayout(wbox)
        wv.setContentsMargins(0, 0, 0, 0)
        wv.setSpacing(6)
        self.watch_state = QLabel()
        self.watch_state.setObjectName("settingDesc")
        self.watch_state.setWordWrap(True)
        self.watch_state.setMinimumWidth(460)
        wbtns = QHBoxLayout()
        wbtns.setSpacing(6)
        for text, ico, fn in (("  Выбрать образцы…", "users", self.pick_watch_profiles),
                              ("  Проверить сейчас", "fingerprint", self.run_engine_watch)):
            b = QPushButton(text)
            set_icon(b, ico, "text", 14)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            wbtns.addWidget(b)
        wbtns.addStretch()
        wv.addWidget(self.watch_state)
        wv.addLayout(wbtns)
        self._row("engine", "Профили-образцы", "На них проверяется маскировка после обновления. Лучше выбрать "
                  "по одному профилю каждого вида (ПК, телефон) с рабочим прокси. Не выбраны — берутся автоматически.",
                  wbox)
        self._show_watch_state()
        self._engine_busy = False
        QTimer.singleShot(300, self.check_engine)

        # ---------- новые профили ----------
        self._h("profiles")
        self._combo("profiles", "Тип по умолчанию", "Какой профиль создаётся по кнопке «+».", "default_kind",
                    [("Компьютер", "desktop"), ("Телефон (Android)", "mobile")])
        self._combo("profiles", "Движок по умолчанию", "Для профилей-компьютеров. Телефоны всегда используют Chromium.",
                    "default_engine", [("Chromium", "chromium"), ("Camoufox (Firefox)", "camoufox")])
        from ...hostos import TITLES, host_os
        self._combo("profiles", "ОС по умолчанию", "Операционная система в отпечатке нового ПК-профиля. Надёжнее всего — "
                    f"ОС этого компьютера ({TITLES[host_os()]}): браузер рисует текст и canvas шрифтами и рендером ПК, "
                    "поэтому профиль на другой ОС заметнее, а fingerprint.com помечает его «Browser Tampering».",
                    "default_os", [(t + ("  — как у этого ПК" if k == host_os() else ""), k)
                                   for t, k in (("Windows", "windows"), ("macOS", "macos"), ("Linux", "linux"))])
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
        self._check("browser", "Спрашивать перед запуском без прокси",
                    "Профиль без прокси открывает сайты с вашего настоящего IP, и все такие профили оказываются на "
                    "одном адресе. Перед запуском из интерфейса — вопрос (планировщик и API не спрашивают).",
                    "warn_no_proxy")
        self._check("browser", "Подтверждать удаление", "Спрашивать перед удалением профилей и прокси.", "confirm_delete")

        # ---------- здоровье и бан ----------
        self._h("health")
        from ... import banwatch
        self._check("health", "Авто-метка «Бан»",
                    "Пока профиль запущен (в том числе в сценарии и без окна), после каждого перехода страница "
                    "сверяется с правилами ниже. Сработало — метка «Бан», причина видна в подсказке метки и в панели "
                    "«Отпечаток», уведомление в Telegram. Сайт этих проверок не видит.", "ban_watch")
        rules_box = QWidget()
        rl = QVBoxLayout(rules_box)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)
        rules_ed = QPlainTextEdit(banwatch.format_rules(banwatch.rules()))
        rules_ed.setMinimumWidth(560)
        rules_ed.setFixedHeight(200)
        rules_err = QLabel()
        rules_err.setObjectName("modalError")
        rules_err.setWordWrap(True)
        rules_err.hide()
        reset = QPushButton("  Встроенные правила")
        set_icon(reset, "refresh", "text", 14)
        reset.setCursor(Qt.PointingHandCursor)
        rl.addWidget(rules_ed)
        rl.addWidget(rules_err)
        rl.addWidget(reset, 0, Qt.AlignLeft)
        rtimer = QTimer(self)
        rtimer.setSingleShot(True)
        rtimer.setInterval(800)

        def save_rules():
            try:
                parsed = banwatch.parse_rules(rules_ed.toPlainText())
            except ValueError as e:
                rules_err.setText(f"Не сохранено: {e}")
                rules_err.show()
                return
            rules_err.hide()
            value = None if parsed == banwatch.DEFAULT_RULES else parsed
            if value != settings.get("ban_rules"):
                self._saved("ban_rules", value)

        rtimer.timeout.connect(save_rules)
        rules_ed.textChanged.connect(rtimer.start)
        reset.clicked.connect(lambda: rules_ed.setPlainText(banwatch.format_rules(banwatch.DEFAULT_RULES)))
        self._row("health", "Признаки бана", "По правилу в строке: «сайт | вид | значение». Виды: url — адрес "
                  "содержит строку; text — на странице есть текст (регистр не важен); captcha — капча на этом сайте "
                  "N запусков профиля подряд. Сайт включает поддомены: facebook.com = m.facebook.com. "
                  "Строки с # — комментарии.", rules_box)

        # ---------- прокси ----------
        self._h("proxies")
        self._check("proxies", "Проверять после добавления",
                    "Сразу проверять новые прокси: страна, таймзона, тип IP (резидентный, мобильный, хостинг) "
                    "и ОС по TCP/IP-отпечатку.", "check_proxy_on_add")
        self._check("proxies", "Проверять прокси перед запуском",
                    "Перед каждым запуском профиля (и в сценариях, проверках, прогреве) прокси проверяется. Не "
                    "отвечает — профиль не запускается: лучше не открыть аккаунт, чем открыть его с ошибкой сети. "
                    "Смена IP и страны пишется в журнал активности профиля.", "proxy_check_on_start")
        self._spin("proxies", "Не перепроверять, сек", "Прокси, проверенный недавно, перед запуском не проверяется "
                   "снова — массовый запуск не ждёт десятки одинаковых проверок.", "proxy_check_cache", 0, 3600)
        self._combo("proxies", "Если прокси сменил страну",
                    "Ротационный прокси выдал IP другой страны: аккаунт «переехал бы». Предупредить — запустить, "
                    "написать в журнал и в лог (таймзона и язык подстроятся по IP); не запускать — остановиться.",
                    "proxy_geo_change", [("Предупредить и запустить", "warn"), ("Не запускать профиль", "block")])
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
        self._combo("rpa", "Сервис SMS", "Для шагов «Получить номер» и «Ждать SMS». sms-activate — и любые сервисы с "
                    "совместимым API (адрес ниже).", "sms_provider",
                    [("5sim", "5sim"), ("sms-activate (совместимый API)", "sms-activate")])
        self._secret("rpa", "Ключ сервиса SMS", "API-ключ из личного кабинета 5sim (токен API) или sms-activate.",
                     "sms_key", check=("Проверить баланс", self._sms_balance))
        from PySide6.QtWidgets import QLineEdit
        sms_url = QLineEdit(settings.get("sms_activate_url"))
        sms_url.setMinimumWidth(460)
        sms_url.editingFinished.connect(lambda: sms_url.text().strip() != settings.get("sms_activate_url") and
                                        self._saved("sms_activate_url", sms_url.text().strip()))
        self._row("rpa", "Адрес API sms-activate", "handler_api.php сервиса с протоколом sms-activate "
                  "(getNumber / getStatus / setStatus). Для 5sim не используется.", sms_url)

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
        self._secret("api", "Ключ API", "Если задан, API принимает только запросы с заголовком X-API-Key: <ключ> "
                     "(или ?token=<ключ>). Запросы со страниц сайтов отклоняются всегда, но ключ защищает и от "
                     "других программ на этом ПК. Пусто — без ключа. Действует сразу.", "api_token",
                     check=("Сгенерировать", self._gen_token))

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
        self._combo("data", "Шифрование резервных копий",
                    "Ключ Windows — копию откроет только этот ПК под этой учётной записью (если ПК сломается — не "
                    "откроет никто). Пароль — копия откроется где угодно с паролем: задайте его ниже и запомните. "
                    "Без шифрования cookies (и пароли, если включены) лежат в копии открытым текстом.",
                    "backup_encryption", [("Ключом Windows этого ПК", "dpapi"), ("Паролем", "password"),
                                          ("Без шифрования", "none")])
        self._secret("data", "Пароль резервных копий", "Для шифрования паролем. Хранится в настройках зашифрованным "
                     "ключом Windows. Забытый пароль не восстановить — копии станут бесполезны.", "backup_password")
        self._check("data", "Пароли и 2FA в резервных копиях",
                    "В базе они зашифрованы ключом Windows этого ПК и на другом компьютере не расшифруются. "
                    "Если включить, они попадут в копию — зашифрованными вместе с ней (или открытым текстом, если "
                    "шифрование выключено).", "backup_secrets")

        # ---------- о программе / обновление ----------
        self._h("about")
        from ... import __version__, selfupdate
        ub = QWidget()
        uv = QVBoxLayout(ub)
        uv.setContentsMargins(0, 0, 0, 0)
        uv.setSpacing(6)
        self.app_state = QLabel(f"claudeAnty {__version__} · " + {
            "installer": "установленная версия", "git": "запуск из исходников (git)",
            "source": "запуск из исходников"}[selfupdate.mode()])
        self.app_state.setObjectName("settingDesc")
        self.app_state.setWordWrap(True)
        self.app_state.setMinimumWidth(460)
        ubtns = QHBoxLayout()
        ubtns.setSpacing(6)
        self.app_check_btn = QPushButton("  Проверить обновления")
        set_icon(self.app_check_btn, "refresh", "text", 14)
        self.app_check_btn.clicked.connect(lambda: self.check_app_update(force=True))
        self.app_update_btn = QPushButton("  Обновить")
        self.app_update_btn.setObjectName("primary")
        self.app_update_btn.clicked.connect(self.install_app_update)
        self.app_skip_btn = QPushButton("Пропустить эту версию")
        self.app_skip_btn.clicked.connect(self.skip_app_update)
        for b in (self.app_check_btn, self.app_update_btn, self.app_skip_btn):
            b.setCursor(Qt.PointingHandCursor)
            ubtns.addWidget(b)
        ubtns.addStretch()
        self.app_update_btn.hide()
        self.app_skip_btn.hide()
        uv.addWidget(self.app_state)
        uv.addLayout(ubtns)
        self._row("about", "Версия и обновление",
                  "Установленная версия обновляется установщиком из релиза на GitHub (контрольная сумма SHA-256 "
                  "проверяется); данные профилей при этом не трогаются. При запуске из исходников — git pull и "
                  "зависимости.", ub)
        self._check("about", "Проверять обновления claudeAnty", "Раз в сутки смотреть, вышла ли новая версия, и "
                    "показывать кнопку в строке состояния. Без вашего нажатия ничего не устанавливается.",
                    "self_update_check")
        self._app_release = None

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

    _SECRETS = {"tg_token", "tg_chat", "captcha_key", "sms_key", "api_token", "backup_password"}

    def _saved(self, key, value, on_change=None):
        settings.set(key, value)
        shown = ("••••" + str(value)[-4:] if value else "пусто") if key in self._SECRETS else \
            (f"{len(value)} правил" if key == "ban_rules" and value else value)
        self.win.log(f"Настройка сохранена: {key} = {shown}")
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
        ed.setProperty("settingKey", key)
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

    def _gen_token(self):
        import secrets
        from PySide6.QtWidgets import QLineEdit
        token = secrets.token_urlsafe(24)
        self._saved("api_token", token)
        for ed in self.findChildren(QLineEdit):
            if ed.echoMode() == QLineEdit.Password and ed.property("settingKey") == "api_token":
                ed.setText(token)
        QApplication.clipboard().setText(token)
        self.win.log("Новый ключ API скопирован в буфер обмена")

    def _sms_balance(self):
        from ...sms import balance
        from ...manager import manager
        from ..workers import run_bg
        run_bg(lambda: manager.run(balance()), lambda r, e: self.win.log(
            f"Сервис SMS: ошибка — {e}" if e else f"Баланс {settings.get('sms_provider')}: {r:.2f}"), self)

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

    # ---------- автопроверка после обновления ----------
    def _show_watch_state(self):
        from ... import db, enginewatch
        chosen = settings.get("engine_watch_profiles") or []
        pids = enginewatch.sample_profiles()
        names = ", ".join(f"#{i} {p.name}" for i in pids if (p := db.get_profile(i))) or "нет подходящих профилей"
        text = f"{'Выбраны' if chosen else 'Автоматически'}: {names}. Версия, с которой сравниваем: " \
               f"{settings.get('engine_seen_version') or '—'}."
        last = settings.get("engine_watch_last") or {}
        if last:
            text += f"\nПоследняя автопроверка {last.get('time', '')} (Chrome {last.get('version', '?')}): " + \
                    ("⚠ стало хуже" if last.get("worse") else "без ухудшений")
        self.watch_state.setText(text)

    def pick_watch_profiles(self):
        from PySide6.QtWidgets import QListWidget, QListWidgetItem
        from ... import db
        from ..modal import Modal
        chosen = set(settings.get("engine_watch_profiles") or [])
        lst = QListWidget()
        lst.setMinimumHeight(260)
        for p in db.list_profiles():
            if p.engine != "chromium":
                continue
            it = QListWidgetItem(f"#{p.id}  {p.name}  ({'телефон' if p.kind == 'mobile' else 'ПК'})")
            it.setData(Qt.UserRole, p.id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if p.id in chosen else Qt.Unchecked)
            lst.addItem(it)
        if not Modal(self.win, "Профили-образцы", lst, ok_text="Сохранить", width=480).exec():
            return
        ids = [lst.item(i).data(Qt.UserRole) for i in range(lst.count()) if lst.item(i).checkState() == Qt.Checked]
        self._saved("engine_watch_profiles", ids)
        self._show_watch_state()

    def run_engine_watch(self):
        from ... import enginewatch
        from ...manager import manager
        from ..workers import run_bg
        self.watch_state.setText("Проверка образцов… (каждый запускается без окна на ~10–20 секунд)")
        self.win.log("Автопроверка профилей-образцов…")

        def done(r, e):
            if e:
                self.win.log(f"Автопроверка: {e}")
            else:
                self.win.log(r["text"])
            self._show_watch_state()

        run_bg(lambda: manager.run(enginewatch.run(manager, progress=manager.scheduler.say), timeout=1800), done, self)

    # ---------- обновление claudeAnty ----------
    def check_app_update(self, force: bool = False):
        from ... import selfupdate
        from ..workers import run_bg
        self.app_check_btn.setEnabled(False)
        self.app_state.setText("Проверка обновлений…")

        def done(res, err):
            self.app_check_btn.setEnabled(True)
            if err:
                self.app_state.setText(f"Ошибка проверки: {err}")
                return
            selfupdate.remember(res)
            self._app_release = res
            text = res["message"]
            if res["available"] and res["release"].get("notes"):
                text += "\n\nЧто нового:\n" + res["release"]["notes"][:800]
            self.app_state.setText(text)
            can = res["available"] and (res["mode"] == "git" or (res["mode"] == "installer"
                                                                  and res["release"].get("asset_url")))
            self.app_update_btn.setVisible(bool(can))
            self.app_skip_btn.setVisible(bool(res["available"]))
            self.win._show_update_notice(selfupdate.pending_notice())

        run_bg(lambda: selfupdate.check(force), done, self)

    def skip_app_update(self):
        if self._app_release and self._app_release.get("latest"):
            self._saved("self_update_skip", self._app_release["latest"])
            self.app_update_btn.hide()
            self.app_skip_btn.hide()
            self.win._show_update_notice(None)

    def install_app_update(self):
        from ... import selfupdate
        from ...manager import manager
        from ..modal import confirm
        from ..workers import run_bg
        res = self._app_release
        if not res or not res.get("available"):
            return
        if res["mode"] == "git":
            self.app_update_btn.setEnabled(False)

            def done_git(msg, err):
                self.app_update_btn.setEnabled(True)
                self.app_state.setText(f"Ошибка обновления: {err}" if err else msg)
                self.win.log(f"Обновление claudeAnty: {err or msg}")

            run_bg(lambda: selfupdate.update_source(manager.scheduler.say), done_git, self)
            return
        running = list(manager.sessions)
        text = f"Скачать и установить claudeAnty {res['latest']}? После загрузки программа закроется и запустится снова."
        if running:
            text += f"\n\nЗапущено профилей: {len(running)} — они будут остановлены (cookies сохраняются)."
        if not confirm(self.win, "Обновление claudeAnty", text, ok_text="Обновить", danger=False):
            return
        self.app_update_btn.setEnabled(False)
        self.app_check_btn.setEnabled(False)

        def done(exe, err):
            if err:
                self.app_update_btn.setEnabled(True)
                self.app_check_btn.setEnabled(True)
                self.app_state.setText(f"Ошибка обновления: {err}")
                self.win.log(f"Обновление claudeAnty: {err}")
                return
            self.win.log("Установщик проверен (SHA-256 совпал), запускаю и закрываю claudeAnty…")
            try:
                manager.run(manager.stop_all(), timeout=60)
            except Exception:  # noqa: BLE001
                pass
            selfupdate.run_installer(exe)
            QApplication.instance().quit()

        run_bg(lambda: selfupdate.download_installer(res["release"], manager.scheduler.say), done, self)

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
