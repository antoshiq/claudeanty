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

    SECTIONS = [("profiles", "Новые профили", "users"), ("browser", "Поведение", "monitor"),
                ("proxies", "Прокси", "globe"), ("warmup", "Прогрев cookies", "flame"), ("ui", "Внешний вид", "layers"),
                ("api", "Локальный API", "plug"), ("data", "Данные", "folder")]

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
                    "Сразу проверять новые прокси и определять страну и таймзону.", "check_proxy_on_add")

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

        # ---------- данные ----------
        self._h("data")
        btn = QPushButton("  Открыть папку данных")
        set_icon(btn, "folder_open", "text", 14)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: os.startfile(str(DATA_DIR)))
        self._row("data", "Папка данных", f"{DATA_DIR}\nЗдесь хранятся база профилей, прокси, настройки и данные браузеров.", btn)

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

    def _accent_changed(self, _value):
        apply_theme(QApplication.instance())
        self.win.restyle()

    # ---------- API страницы ----------
    def scroll_to(self, key: str):
        h = self.headers.get(key)
        if h:
            self.scroll.verticalScrollBar().setValue(h.y())

    def refresh(self):
        q = self.win.search.text().lower().strip()
        for row, text in self.rows:
            row.setVisible(not q or q in text)
