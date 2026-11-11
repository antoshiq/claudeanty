"""Форма профиля (модальное окно поверх главного): вкладки «Основное», «Отпечаток», «Запуск»."""
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QPlainTextEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from .. import db, settings
from ..fingerprints.generator import (CORES, DEVICES, MEMORY, SCREENS, apply_edit, generate, gpus_for, validate)
from ..models import STATUSES, Profile
from .icons import icon
from .modal import Modal
from .theme import C

WEBRTC = [("Только через прокси (рекомендуется)", "proxy"), ("Отключён", "off"), ("Как есть (реальный IP)", "real")]


def _label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("formLabel")
    return lbl


def _form() -> QFormLayout:
    f = QFormLayout()
    f.setContentsMargins(0, 12, 0, 0)
    f.setHorizontalSpacing(16)
    f.setVerticalSpacing(10)
    return f


def _hint(text: str = "") -> QLabel:
    h = QLabel(text)
    h.setObjectName("hint")
    h.setWordWrap(True)
    return h


def _timezones() -> list[str]:
    try:
        import zoneinfo
        return sorted(zoneinfo.available_timezones())
    except Exception:  # noqa: BLE001
        return []


class ProfileForm(QWidget):
    def __init__(self, profile: Optional[Profile] = None):
        super().__init__()
        self.profile = profile
        self.fp: dict = dict(profile.fingerprint) if profile else {}
        self._loading = True

        self.tabs = QTabWidget()
        self.tabs.setObjectName("bottomPanel")  # тот же стиль, что у нижней панели
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.addTab(self._main_tab(), "ОСНОВНОЕ")
        self.tabs.addTab(self._fp_tab(), "ОТПЕЧАТОК")
        self.tabs.addTab(self._launch_tab(), "ЗАПУСК")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.tabs)

        if profile:
            self._load(profile)
        else:
            self._defaults()
        self._loading = False
        self._identity_changed(initial=True)

    # ---------- вкладки ----------
    def _main_tab(self) -> QWidget:
        w = QWidget()
        self.name = QLineEdit()
        self.name.setPlaceholderText("Например: Facebook 1")
        self.kind = QComboBox()
        self.kind.addItem(icon("monitor", C["text"], 14), "Компьютер", "desktop")
        self.kind.addItem(icon("phone", C["text"], 14), "Телефон (Android)", "mobile")
        self.engine = QComboBox()
        self.engine.addItem("Chromium", "chromium")
        self.engine.addItem("Camoufox (Firefox)", "camoufox")
        self.os = QComboBox()
        for label, v in (("Windows", "windows"), ("macOS", "macos"), ("Linux", "linux")):
            self.os.addItem(label, v)
        self.device = QComboBox()
        self.device.addItem("Случайное устройство", "")
        for k, v in DEVICES.items():
            self.device.addItem(v["label"], k)
        self.proxy = QComboBox()
        self.proxy.addItem("Без прокси", None)
        for px in db.list_proxies():
            mark = {"ok": "✓ ", "fail": "✗ "}.get(px.status, "")
            extra = f"  · {px.tcp_os}" if px.tcp_os else ""
            self.proxy.addItem(f"{mark}{px.type}://{px.host}:{px.port}  {px.country}{extra}".strip(), px.id)
        self.status = QComboBox()
        for key, (label, color) in STATUSES.items():
            self.status.addItem(_dot(color), label, key)
        self.folder = QLineEdit()
        self.folder.setPlaceholderText("Без папки")
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("через запятую")
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(56)
        self.mobile_info = _hint("Телефон эмулируется поверх Chromium: Android UA, Client Hints, экран, touch, "
                                 "сенсоры и GPU телефона. Шрифты и часть аппаратного поведения ПК полностью не скрываются.")
        f = _form()
        f.addRow(_label("Название"), self.name)
        f.addRow(_label("Тип"), self.kind)
        f.addRow(_label("Движок"), self.engine)
        f.addRow(_label("ОС"), self.os)
        f.addRow(_label("Устройство"), self.device)
        f.addRow(_label("Прокси"), self.proxy)
        self.proxy_warn = _hint()
        self.proxy_warn.setObjectName("modalError")
        f.addRow("", self.proxy_warn)
        self.proxy.currentIndexChanged.connect(lambda _: self._proxy_check())
        f.addRow(_label("Метка"), self.status)
        f.addRow(_label("Папка"), self.folder)
        f.addRow(_label("Теги"), self.tags)
        f.addRow(_label("Заметки"), self.notes)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(f)
        v.addWidget(self.mobile_info)
        for cb in (self.kind, self.engine, self.os, self.device):
            cb.currentIndexChanged.connect(lambda _: self._identity_changed())
        return w

    def _fp_tab(self) -> QWidget:
        w = QWidget()
        self.screen = QComboBox()
        for sw, sh in SCREENS:
            self.screen.addItem(f"{sw} × {sh}", f"{sw}x{sh}")
        self.cores = QComboBox()
        for c in CORES:
            self.cores.addItem(f"{c} ядер", c)
        self.memory = QComboBox()
        for m in MEMORY:
            self.memory.addItem(f"{m} ГБ", m)
        self.gpu = QComboBox()
        self.webrtc = QComboBox()
        for label, v in WEBRTC:
            self.webrtc.addItem(label, v)
        self.auto_geo = QCheckBox("Таймзона, язык и геолокация по IP (прокси или ваш IP)")
        self.locale = QLineEdit()
        self.locale.setPlaceholderText("ru-RU")
        self.timezone = QLineEdit()
        self.timezone.setPlaceholderText("Europe/Moscow")
        zones = _timezones()
        if zones:
            comp = QCompleter(zones)
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setFilterMode(Qt.MatchContains)
            self.timezone.setCompleter(comp)
        regen = QPushButton("  Сгенерировать заново")
        regen.setIcon(icon("refresh", C["text"], 14))
        regen.setCursor(Qt.PointingHandCursor)
        regen.clicked.connect(self._regenerate)
        self.fp_note = _hint()
        self.problems = QLabel()
        self.problems.setObjectName("modalError")
        self.problems.setWordWrap(True)

        f = _form()
        f.addRow(_label("Экран"), self.screen)
        f.addRow(_label("Процессор"), self.cores)
        f.addRow(_label("Память"), self.memory)
        f.addRow(_label("Видеокарта"), self.gpu)
        f.addRow(_label("WebRTC"), self.webrtc)
        f.addRow("", self.auto_geo)
        f.addRow(_label("Язык"), self.locale)
        f.addRow(_label("Таймзона"), self.timezone)
        row = QHBoxLayout()
        row.addWidget(regen)
        row.addStretch()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(f)
        v.addSpacing(6)
        v.addLayout(row)
        v.addWidget(self.fp_note)
        v.addWidget(self.problems)
        v.addStretch()
        for cb in (self.screen, self.cores, self.memory, self.gpu):
            cb.currentIndexChanged.connect(lambda _: self._recheck())
        for le in (self.locale, self.timezone):
            le.textChanged.connect(lambda _: self._recheck())
        self.auto_geo.toggled.connect(self._geo_toggled)
        return w

    def _launch_tab(self) -> QWidget:
        w = QWidget()
        self.start_urls = QPlainTextEdit()
        self.start_urls.setPlaceholderText("https://example.com\nhttps://mail.google.com")
        self.start_urls.setFixedHeight(70)
        self.bookmarks = QPlainTextEdit()
        self.bookmarks.setPlaceholderText("Google | https://google.com\nhttps://facebook.com")
        self.bookmarks.setFixedHeight(70)
        self.exts = QListWidget()
        self.exts.setObjectName("boxList")
        self.exts.setFixedHeight(76)
        add = QPushButton("  Добавить папку…")
        add.setIcon(icon("plus", C["text"], 14))
        add.clicked.connect(self._add_ext)
        rm = QPushButton("Убрать")
        rm.clicked.connect(lambda: [self.exts.takeItem(self.exts.row(i)) for i in self.exts.selectedItems()])
        for b in (add, rm):
            b.setCursor(Qt.PointingHandCursor)
        brow = QHBoxLayout()
        brow.addWidget(add)
        brow.addWidget(rm)
        brow.addStretch()
        ext_box = QWidget()
        el = QVBoxLayout(ext_box)
        el.setContentsMargins(0, 0, 0, 0)
        el.addWidget(self.exts)
        el.addLayout(brow)
        f = _form()
        f.addRow(_label("Стартовые\nстраницы"), self.start_urls)
        f.addRow(_label("Закладки"), self.bookmarks)
        f.addRow(_label("Расширения"), ext_box)
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(f)
        v.addWidget(_hint("Стартовые страницы открываются при каждом запуске. Закладки — по одной в строке, "
                          "«Название | адрес» или просто адрес (только Chromium). Расширения — распакованные папки "
                          "с manifest.json: для Chromium — расширения Chrome, для Camoufox — Firefox."))
        v.addStretch()
        return w

    # ---------- данные ----------
    def _defaults(self):
        self.kind.setCurrentIndex(max(0, self.kind.findData(settings.get("default_kind"))))
        self.engine.setCurrentIndex(max(0, self.engine.findData(settings.get("default_engine"))))
        self.os.setCurrentIndex(max(0, self.os.findData(settings.get("default_os"))))
        self.device.setCurrentIndex(max(0, self.device.findData(settings.get("default_device"))))
        self.auto_geo.setChecked(bool(settings.get("default_auto_geo")))
        self.status.setCurrentIndex(max(0, self.status.findData("new")))
        self.locale.setText("en-US")

    def _load(self, p: Profile):
        self.name.setText(p.name)
        self.kind.setCurrentIndex(self.kind.findData(p.kind))
        self.engine.setCurrentIndex(self.engine.findData(p.engine))
        if p.os != "android":
            self.os.setCurrentIndex(self.os.findData(p.os))
        self.device.setCurrentIndex(max(0, self.device.findData(p.device)))
        self.proxy.setCurrentIndex(max(0, self.proxy.findData(p.proxy_id)))
        self.status.setCurrentIndex(max(0, self.status.findData(p.status)))
        self.folder.setText(p.folder)
        self.tags.setText(", ".join(p.tags))
        self.notes.setPlainText(p.notes)
        self.webrtc.setCurrentIndex(max(0, self.webrtc.findData(p.webrtc)))
        self.auto_geo.setChecked(p.auto_geo)
        self.locale.setText(p.locale)
        self.timezone.setText(p.timezone)
        self.start_urls.setPlainText("\n".join(p.start_urls))
        self.bookmarks.setPlainText("\n".join(f"{b.get('title')} | {b['url']}" if b.get("title") else b["url"]
                                              for b in p.bookmarks))
        for e in p.extensions:
            self.exts.addItem(e)
        self._orig_identity = (p.kind, p.engine, p.os, p.device)

    def _identity(self):
        mobile = self.kind.currentData() == "mobile"
        return (self.kind.currentData(), "chromium" if mobile else self.engine.currentData(),
                "android" if mobile else self.os.currentData(), (self.device.currentData() or "") if mobile else "")

    def _identity_changed(self, initial=False):
        if self._loading:
            return
        mobile = self.kind.currentData() == "mobile"
        self.device.setEnabled(mobile)
        self.os.setEnabled(not mobile)
        self.engine.setEnabled(not mobile)
        if mobile and self.engine.currentData() != "chromium":
            self.engine.setCurrentIndex(0)
        self.mobile_info.setVisible(mobile)
        self._proxy_check()
        # тип/ОС/устройство поменялись — нужен новый отпечаток
        same = self.profile and self._identity() == getattr(self, "_orig_identity", None)
        if not self.fp or (not initial and not same):
            self._regenerate()
        elif same and self.profile:
            self.fp = dict(self.profile.fingerprint)
        self._fill_fp_fields()

    def _proxy_check(self):
        from ..proxy.checker import os_warning
        if not hasattr(self, "proxy_warn"):
            return
        px = db.get_proxy(self.proxy.currentData())
        _, _, os_name, _ = self._identity()
        warn = os_warning(os_name, px)
        self.proxy_warn.setText(warn)
        self.proxy_warn.setVisible(bool(warn))

    def _regenerate(self):
        kind, _, os_name, device = self._identity()
        self.fp = generate(kind, os_name, device)
        self._fill_fp_fields()

    def _fill_fp_fields(self):
        self._loading = True
        mobile = self.kind.currentData() == "mobile"
        camoufox = self.engine.currentData() == "camoufox"
        fp = self.fp
        os_name = "android" if mobile else self.os.currentData()
        self.gpu.clear()
        self._gpu_vendor = {}
        for vendor, renderer in gpus_for(os_name):
            self.gpu.addItem(renderer, renderer)  # ключи-строки: QComboBox.findData не сравнивает кортежи
            self._gpu_vendor[renderer] = vendor
        sc = fp.get("screen", {})
        key = f"{sc.get('width')}x{sc.get('height')}"
        if sc and self.screen.findData(key) < 0:
            self.screen.addItem(f"{sc['width']} × {sc['height']}", key)
        self.screen.setCurrentIndex(max(0, self.screen.findData(key)))
        self.cores.setCurrentIndex(max(0, self.cores.findData(fp.get("cores"))))
        self.memory.setCurrentIndex(max(0, self.memory.findData(fp.get("memory"))))
        gl = fp.get("webgl", {})
        if gl.get("renderer") and self.gpu.findData(gl["renderer"]) < 0:
            self.gpu.addItem(gl["renderer"], gl["renderer"])
            self._gpu_vendor[gl["renderer"]] = gl.get("vendor", "")
        self.gpu.setCurrentIndex(max(0, self.gpu.findData(gl.get("renderer"))))
        editable = not camoufox
        self.screen.setEnabled(editable and not mobile)
        self.gpu.setEnabled(editable and not mobile)
        self.cores.setEnabled(editable)
        self.memory.setEnabled(editable)
        if camoufox:
            self.fp_note.setText("Camoufox генерирует отпечаток железа сам (на уровне движка), поэтому экран, "
                                 "процессор и видеокарта здесь не редактируются. WebRTC, язык и таймзона работают.")
        elif mobile:
            self.fp_note.setText("Экран и видеокарта определяются выбранным телефоном.")
        else:
            self.fp_note.setText("Значения проверяются на согласованность. Окно браузера подстраивается под экран.")
        self._loading = False
        self._geo_toggled(self.auto_geo.isChecked())

    def _geo_toggled(self, on: bool):
        self.locale.setEnabled(not on)
        self.timezone.setEnabled(not on)
        self._recheck()

    def _edited_fp(self) -> dict:
        sk = self.screen.currentData()
        screen = tuple(int(x) for x in sk.split("x")) if sk else None
        r = self.gpu.currentData()
        gpu = (self._gpu_vendor.get(r, ""), r) if r else None
        return apply_edit(self.fp, screen=screen, cores=self.cores.currentData(),
                          memory=self.memory.currentData(), gpu=gpu)

    def problems_list(self) -> list[str]:
        kind, engine, os_name, _ = self._identity()
        fp = self._edited_fp() if engine == "chromium" else self.fp
        errs = validate(kind, os_name, fp, self.locale.text().strip(), self.timezone.text().strip(),
                        self.auto_geo.isChecked())
        if engine == "camoufox":  # железо Camoufox не редактируется — проверяем только язык и таймзону
            errs = [e for e in errs if "Язык" in e or "таймзона" in e]
        return errs

    def _recheck(self):
        if self._loading:
            return
        errs = self.problems_list()
        self.problems.setText("\n".join("• " + e for e in errs))
        self.problems.setVisible(bool(errs))

    def validate(self) -> Optional[str]:
        errs = self.problems_list()
        if errs:
            self.tabs.setCurrentIndex(1)
            return "Отпечаток несогласован: " + "; ".join(errs)
        return None

    def _add_ext(self):
        d = QFileDialog.getExistingDirectory(self, "Папка распакованного расширения (с manifest.json)")
        if not d:
            return
        if not Path(d, "manifest.json").exists():
            self.problems.setText("• В папке нет manifest.json — нужна распакованная папка расширения")
            self.problems.show()
            return
        self.exts.addItem(d)

    def result_profile(self) -> Profile:
        p = self.profile.model_copy(deep=True) if self.profile else Profile(name="")
        kind, engine, os_name, device = self._identity()
        p.name = self.name.text().strip() or "Profile"
        p.kind, p.engine, p.os, p.device = kind, engine, os_name, device
        p.proxy_id = self.proxy.currentData()
        p.status = self.status.currentData() or ""
        p.folder = self.folder.text().strip()
        p.tags = [t.strip() for t in self.tags.text().split(",") if t.strip()]
        p.notes = self.notes.toPlainText()
        p.webrtc = self.webrtc.currentData()
        p.auto_geo = self.auto_geo.isChecked()
        if not p.auto_geo:
            p.locale = self.locale.text().strip() or "en-US"
            p.timezone = self.timezone.text().strip()
        fp = self._edited_fp() if engine == "chromium" else dict(self.fp)
        if engine == "camoufox" and self.profile and self._identity() == getattr(self, "_orig_identity", None):
            fp["camoufox"] = self.profile.fingerprint.get("camoufox")
        p.fingerprint = {k: v for k, v in fp.items() if v is not None}
        p.start_urls = [u.strip() for u in self.start_urls.toPlainText().splitlines() if u.strip()]
        bms = []
        for line in self.bookmarks.toPlainText().splitlines():
            if not line.strip():
                continue
            title, _, url = line.rpartition("|") if "|" in line else ("", "", line)
            bms.append({"title": title.strip(), "url": url.strip() if "://" in url else "https://" + url.strip()})
        p.bookmarks = bms
        p.extensions = [self.exts.item(i).text() for i in range(self.exts.count())]
        return p


def _dot(color: str):
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
    pm = QPixmap(12, 12)
    pm.fill(Qt.transparent)
    pa = QPainter(pm)
    pa.setRenderHint(QPainter.Antialiasing)
    pa.setBrush(QColor(color))
    pa.setPen(Qt.NoPen)
    pa.drawEllipse(1, 1, 10, 10)
    pa.end()
    return QIcon(pm)


def profile_modal(host, profile: Optional[Profile] = None) -> Optional[Profile]:
    form = ProfileForm(profile)
    m = Modal(host, "Изменить профиль" if profile else "Новый профиль", form,
              ok_text="Сохранить" if profile else "Создать", width=600, validate=form.validate)
    return form.result_profile() if m.exec() else None
