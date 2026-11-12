"""Массовые действия с профилями, массовое создание с прокси, ячейка «Проверка», быстрые фильтры,
мастер первого запуска."""
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .. import db, settings
from ..models import Profile, Proxy
from .icons import icon
from .modal import Modal
from .theme import C


def _label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("formLabel")
    return lbl


def _hint(text: str = "", error: bool = False) -> QLabel:
    h = QLabel(text)
    h.setObjectName("modalError" if error else "hint")
    h.setWordWrap(True)
    return h


def warn_color() -> str:
    return "#9a6700" if QColor(C["editor"]).lightness() > 128 else "#d29922"


# ---------------- ячейка «Проверка» и быстрые фильтры ----------------
def check_state(p: Profile) -> tuple[str, str, str, int]:
    """-> (текст, цвет, подсказка, уровень: 0 — не проверялся, 1 — ок, 2 — !, 3 — ✗)."""
    lc, le = p.last_check or {}, p.last_external or {}
    if not lc and not le:
        return "—", C["faint"], "Ещё не проверялся — кнопка с отпечатком пальца", 0
    items = [i for i in lc.get("items", [])] + [i for s in (le.get("sites") or {}).values() for i in s.get("items", [])]
    ok = sum(1 for s, *_ in items if s == "ok")
    warn = sum(1 for s, *_ in items if s == "warn")
    fail = sum(1 for s, *_ in items if s == "fail")
    tip = []
    if lc:
        tip.append(f"Проверка {lc.get('time')}: ✓ {lc.get('ok')} из {lc.get('total')}, ✗ {lc.get('fail')}")
    if le:
        tip.append(f"Детекторы {le.get('time')}: ✓ {le.get('ok', 0)}  ! {le.get('warn', 0)}  ✗ {le.get('fail', 0)}")
    tip += [f"✗ {t}" for s, t, *_ in items if s == "fail"][:8]
    if fail:
        return f"✗ {fail}", C["red"], "\n".join(tip), 3
    if warn:
        return f"! {ok}/{len(items)}", warn_color(), "\n".join(tip), 2
    return f"✓ {ok}/{len(items)}", C["green"], "\n".join(tip), 1


QUICK = {"problems": ("С проблемами", "#f85149"), "no_proxy": ("Без прокси", "#d29922"),
         "ban": ("В бане", "#f85149"), "running": ("Запущенные", "#3fb950"), "unchecked": ("Не проверялись", "#6e7681")}


def quick_match(key: str, p: Profile, proxies: dict, running: bool) -> bool:
    px = proxies.get(p.proxy_id)
    if key == "problems":
        return check_state(p)[3] == 3 or bool(p.proxy_id and (not px or px.status == "fail"))
    if key == "no_proxy":
        return not p.proxy_id or not px
    if key == "ban":
        return p.status == "ban"
    if key == "running":
        return running
    if key == "unchecked":
        return check_state(p)[3] == 0
    return True


# ---------------- массовые действия ----------------
def move_to_folder(host, pids: list[int], log) -> bool:
    folders = sorted({p.folder for p in db.list_profiles() if p.folder})
    w = QWidget()
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 0)
    combo = QComboBox()
    combo.setEditable(True)
    combo.addItem("")
    combo.addItems(folders)
    combo.lineEdit().setPlaceholderText("Название папки — новая или из списка; пусто — убрать из папки")
    v.addWidget(combo)
    v.addWidget(_hint(f"Профилей: {len(pids)}. Папка появится в боковой панели слева."))
    if not Modal(host, "Переместить в папку", w, ok_text="Переместить", width=480).exec():
        return False
    name = combo.currentText().strip()
    from ..activity import log as alog
    for pid in pids:
        p = db.get_profile(pid)
        if p and p.folder != name:
            alog(pid, "edit", f"папка «{p.folder or '—'}» → «{name or '—'}»")
            p.folder = name
            db.save_profile(p)
    log(f"Папка «{name or 'без папки'}» — профилей: {len(pids)}")
    return True


def edit_tags(host, pids: list[int], log) -> bool:
    profiles = [p for i in pids if (p := db.get_profile(i))]
    have = sorted({t for p in profiles for t in p.tags})
    w = QWidget()
    f = QFormLayout(w)
    f.setContentsMargins(0, 0, 0, 0)
    add = QLineEdit()
    add.setPlaceholderText("через запятую: fb, us, warm")
    rm = QLineEdit()
    rm.setPlaceholderText("через запятую")
    f.addRow(_label("Добавить"), add)
    f.addRow(_label("Убрать"), rm)
    f.addRow("", _hint(f"Профилей: {len(profiles)}. Сейчас у них теги: {', '.join(have) or 'нет'}"))
    if not Modal(host, "Теги профилей", w, ok_text="Применить", width=520).exec():
        return False
    plus = [t.strip() for t in add.text().split(",") if t.strip()]
    minus = {t.strip() for t in rm.text().split(",") if t.strip()}
    for p in profiles:
        tags = [t for t in p.tags if t not in minus] + [t for t in plus if t not in p.tags]
        if tags != p.tags:
            p.tags = tags
            db.save_profile(p)
    log(f"Теги: +{', '.join(plus) or '—'} −{', '.join(minus) or '—'} — профилей: {len(profiles)}")
    return True


# ---------------- массовое создание с прокси ----------------
class BulkForm(QWidget):
    def __init__(self, tpl: Optional[Profile]):
        super().__init__()
        from .pages.profiles import next_number
        self.tpl = tpl
        base = (tpl.name.rstrip("0123456789 ") if tpl else "") or "Profile"
        f = QFormLayout(self)
        f.setContentsMargins(0, 0, 0, 0)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(10)
        self.base = QLineEdit(base)
        self.count = QSpinBox()
        self.count.setRange(1, 500)
        self.count.setValue(5)
        self.mode = QComboBox()
        self.mode.addItem("Без прокси", "none")
        self.mode.addItem("Свободные прокси из списка — по одному на профиль", "free")
        self.mode.addItem("Вставить список прокси — по одному на профиль", "paste")
        self.paste = QPlainTextEdit()
        self.paste.setPlaceholderText("socks5://user:pass@host:port\nhost:port:user:pass")
        self.paste.setFixedHeight(110)
        self.info = _hint()
        f.addRow(_label("Название"), self.base)
        f.addRow(_label("Сколько"), self.count)
        f.addRow(_label("Прокси"), self.mode)
        f.addRow("", self.paste)
        f.addRow("", self.info)
        from ..hostos import warning
        os_warn = warning(tpl.kind, tpl.os) if tpl else warning(settings.get("default_kind"), settings.get("default_os"))
        if os_warn:
            f.addRow("", _hint("⚠ " + os_warn, error=True))
        self._next = next_number
        self.free = self._free_proxies()
        for sig in (self.base.textChanged, self.count.valueChanged, self.mode.currentIndexChanged,
                    self.paste.textChanged):
            sig.connect(lambda *_: self._update())
        self.mode.setCurrentIndex(1 if self.free else 0)
        self._update()

    @staticmethod
    def _free_proxies() -> list[Proxy]:
        used = {p.proxy_id for p in db.list_profiles() if p.proxy_id}
        return [px for px in db.list_proxies() if px.id not in used and px.status != "fail"]

    def _pasted(self) -> tuple[list[Proxy], list[int]]:
        out, bad = [], []
        for i, line in enumerate(self.paste.toPlainText().splitlines(), 1):
            if line.strip():
                try:
                    out.append(Proxy.parse(line))
                except ValueError:
                    bad.append(i)
        return out, bad

    def _update(self):
        mode, n = self.mode.currentData(), self.count.value()
        self.paste.setVisible(mode == "paste")
        start = self._next(self.base.text().strip() or "Profile")
        names = f"«{self.base.text().strip() or 'Profile'} {start}» … «… {start + n - 1}»"
        what = f"Копии профиля «{self.tpl.name}» с новыми отпечатками" if self.tpl else "Профили с настройками по умолчанию"
        if mode == "free":
            extra = f"Свободных рабочих прокси: {len(self.free)}."
        elif mode == "paste":
            px, bad = self._pasted()
            extra = f"Прокси в списке: {len(px)}" + (f", не разобраны строки {', '.join(map(str, bad[:8]))}" if bad else "")
        else:
            extra = "Без прокси сайты увидят ваш настоящий IP — назначить можно позже (меню → «Назначить прокси»)."
        self.info.setText(f"{what}: {names}. {extra}")

    def proxies(self) -> list[Proxy]:
        mode = self.mode.currentData()
        if mode == "free":
            return self.free[:self.count.value()]
        if mode == "paste":
            return self._pasted()[0]
        return []

    def validate(self) -> Optional[str]:
        mode, n = self.mode.currentData(), self.count.value()
        if mode == "paste":
            px, bad = self._pasted()
            if bad:
                return f"Не удалось разобрать строки: {', '.join(map(str, bad[:10]))}"
            if len(px) < n:
                return f"Прокси {len(px)}, профилей {n}: добавьте прокси или уменьшите количество"
        if mode == "free" and len(self.free) < n:
            return f"Свободных прокси {len(self.free)}, профилей {n}: добавьте прокси на вкладке «Прокси» или уменьшите количество"
        return None


def bulk_create(host, tpl: Optional[Profile], manager, log) -> int:
    form = BulkForm(tpl)
    modal = Modal(host, "Массовое создание", form, ok_text="Создать", width=600, validate=form.validate)
    for sig in (form.count.valueChanged, form.mode.currentIndexChanged, form.paste.textChanged):
        sig.connect(lambda *_: modal.error.hide())  # старая ошибка не висит, когда её уже исправили
    if not modal.exec():
        return 0
    from .pages.profiles import next_number
    n = form.count.value()
    base = form.base.text().strip() or "Profile"
    proxies = form.proxies()
    new_proxies = []
    if form.mode.currentData() == "paste":
        proxies = [db.save_proxy(px) for px in proxies[:n]]
        new_proxies = proxies
    if not tpl:
        tpl = Profile(name=base, kind=settings.get("default_kind"), engine=settings.get("default_engine"),
                      os=settings.get("default_os"), device=settings.get("default_device"),
                      auto_geo=settings.get("default_auto_geo"), status="new")
    start = next_number(base)
    for i in range(n):
        p = tpl.model_copy(deep=True)
        p.id = None
        p.fingerprint, p.last_check, p.last_external, p.fp_history = {}, {}, {}, []
        p.ban_reason, p.captcha_streak, p.warm_plan = "", {}, {}
        p.name = f"{base} {start + i}"
        if i < len(proxies):
            p.proxy_id = proxies[i].id
        manager.create_profile(p)
    log(f"Создано профилей: {n}" + (f", с прокси: {min(n, len(proxies))}" if proxies else ", без прокси"))
    if new_proxies and settings.get("check_proxy_on_add"):
        from ..proxy.checker import check_proxy
        from .workers import run_bg

        async def check_all():
            import asyncio
            res = await asyncio.gather(*(check_proxy(px) for px in new_proxies), return_exceptions=True)
            for px in new_proxies:
                db.save_proxy(px)
            return sum(1 for r in res if isinstance(r, dict) and r.get("ok"))

        run_bg(lambda: manager.run(check_all(), timeout=300),
               lambda ok, e: log(f"Проверка новых прокси: {e}" if e else
                                 f"Новые прокси проверены: работают {ok} из {len(new_proxies)}"), host)
    return n


# ---------------- импорт аккаунтов строками ----------------
SERVICES = [("Google", "https://accounts.google.com"), ("Facebook", "https://www.facebook.com/login"),
            ("Instagram", "https://www.instagram.com/accounts/login/"), ("TikTok", "https://www.tiktok.com/login"),
            ("X (Twitter)", "https://x.com/login"), ("Amazon", "https://www.amazon.com/ap/signin"),
            ("Reddit", "https://www.reddit.com/login/"), ("Discord", "https://discord.com/login")]


class AccountsImportForm(QWidget):
    def __init__(self, tpl: Optional[Profile]):
        super().__init__()
        from .. import accimport
        self.tpl = tpl
        self.rows: list = []
        f = QFormLayout(self)
        f.setContentsMargins(0, 0, 0, 0)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(10)
        self.template = QComboBox()
        self.template.setEditable(True)
        for t in (accimport.DEFAULT_TEMPLATE, "login:pass:proxy", "login:pass:2fa", "login:pass:email:email_pass:2fa:proxy",
                  "login|pass|2fa|proxy", "login;pass;email;email_pass;proxy"):
            self.template.addItem(t)
        self.template.setToolTip("Поля: login, pass, 2fa, proxy, email, email_pass, name, notes, - (пропустить).\n"
                                 "Разделитель — : | ; или табуляция. Если в пароле есть «:», выберите шаблон с «|».")
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("user@gmail.com:Pa55word:JBSWY3DPEHPK3PXP:socks5://u:p@1.2.3.4:1080\n"
                                     "user2@gmail.com:Pa55word:JBSWY3DPEHPK3PXP:1.2.3.4:8000:u:p\n"
                                     "user3@gmail.com:Pa55word")
        self.text.setMinimumHeight(170)
        from_file = QPushButton("  Из файла…")
        from_file.setIcon(icon("folder_open", C["text"], 14))
        from_file.setCursor(Qt.PointingHandCursor)
        from_file.clicked.connect(self._load_file)
        self.service = QComboBox()
        self.service.setEditable(True)
        self.service.addItem("", "")
        for name, url in SERVICES:
            self.service.addItem(name, url)
        self.service.lineEdit().setPlaceholderText("Сервис аккаунтов: Google, Facebook…")
        self.naming = QComboBox()
        self.naming.addItem("Название профиля — логин", "login")
        self.naming.addItem("Название профиля — «Profile N»", "number")
        self.folder = QComboBox()
        self.folder.setEditable(True)
        self.folder.addItem("")
        self.folder.addItems(sorted({p.folder for p in db.list_profiles() if p.folder}))
        self.folder.lineEdit().setPlaceholderText("Папка (необязательно)")
        self.fallback = QComboBox()
        self.fallback.addItem("Строки без прокси — свободный прокси из списка", "free")
        self.fallback.addItem("Строки без прокси — без прокси", "none")
        self.info = _hint()
        top = QHBoxLayout()
        top.addWidget(self.template, 1)
        top.addWidget(from_file)
        f.addRow(_label("Формат строки"), top)
        f.addRow(_label("Аккаунты"), self.text)
        f.addRow(_label("Сервис"), self.service)
        f.addRow(_label("Профили"), self.naming)
        f.addRow(_label("Папка"), self.folder)
        f.addRow(_label("Прокси"), self.fallback)
        f.addRow("", self.info)
        what = f"Профили — копии «{tpl.name}» с новыми отпечатками." if tpl else "Профили с настройками по умолчанию."
        f.addRow("", _hint(what + " Логин, пароль и 2FA попадут в «Пароли и 2FA» профиля, почта с паролем — "
                           "отдельным аккаунтом «Почта» (для шага «Ждать письмо»). Одинаковые прокси не дублируются."))
        self.free = BulkForm._free_proxies()
        for sig in (self.template.currentTextChanged, self.text.textChanged, self.fallback.currentIndexChanged):
            sig.connect(lambda *_: self._update())
        self._update()

    def _load_file(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(self, "Файл с аккаунтами", "", "Текст (*.txt *.csv);;Все файлы (*)")
        if path:
            from pathlib import Path
            raw = Path(path).read_bytes()
            for enc in ("utf-8-sig", "cp1251"):
                try:
                    self.text.setPlainText(raw.decode(enc))
                    break
                except UnicodeDecodeError:
                    continue

    def _parse(self):
        from .. import accimport
        return accimport.parse_lines(self.text.toPlainText(), self.template.currentText())

    def _update(self):
        try:
            rows, errors = self._parse()
        except ValueError as e:
            self.rows, self.errors = [], []
            self.info.setText(f"Шаблон: {e}")
            return
        self.rows, self.errors = rows, errors
        if not rows and not errors:
            self.info.setText("Вставьте строки — по одному аккаунту на строку. Пустые строки и # пропускаются.")
            return
        with_px = sum(1 for r in rows if r.proxy)
        no_px = len(rows) - with_px
        with_2fa = sum(1 for r in rows if r.totp)
        parts = [f"Профилей будет: {len(rows)}", f"с 2FA: {with_2fa}", f"со своим прокси: {with_px}"]
        if no_px:
            if self.fallback.currentData() == "free":
                parts.append(f"без прокси в строке: {no_px} (свободных в списке: {len(self.free)}"
                             + (f" — {no_px - len(self.free)} останутся без прокси)" if no_px > len(self.free) else ")"))
            else:
                parts.append(f"без прокси: {no_px}")
        txt = ", ".join(parts) + "."
        if rows:
            r = rows[0]
            txt += f"\nПервая строка: логин «{r.login or r.email}», пароль {'•' * min(len(r.password), 8) or '—'}, " \
                   f"2FA {'есть' if r.totp else 'нет'}, прокси {r.proxy.host + ':' + str(r.proxy.port) if r.proxy else 'нет'}" \
                   + (f", почта {r.email}" if r.email else "")
        if errors:
            txt += "\nОшибки: " + "; ".join(f"строка {n}: {e}" for n, e in errors[:5]) + ("…" if len(errors) > 5 else "")
        self.info.setText(txt)
        self.info.setObjectName("modalError" if errors else "hint")
        self.info.style().unpolish(self.info)
        self.info.style().polish(self.info)

    def validate(self) -> Optional[str]:
        try:
            self._parse()
        except ValueError as e:
            return f"Шаблон: {e}"
        if self.errors:
            return f"Исправьте строки: {', '.join(str(n) for n, _ in self.errors[:10])}"
        if not self.rows:
            return "Нет ни одной строки с аккаунтом"
        if len(self.rows) > 1000:
            return "Не больше 1000 строк за раз"
        return None


def import_accounts(host, tpl: Optional[Profile], manager, log) -> int:
    from .. import accimport
    form = AccountsImportForm(tpl)
    modal = Modal(host, "Импорт аккаунтов строками", form, ok_text="Создать профили", width=720,
                  validate=form.validate)
    form.text.textChanged.connect(lambda: modal.error.hide())
    if not modal.exec():
        return 0
    defaults = {k: settings.get(f"default_{k}") for k in ("kind", "engine", "os", "device", "auto_geo")}
    res = accimport.create_profiles(
        manager, form.rows, service=form.service.currentText().strip(),
        url=form.service.currentData() if form.service.currentText() == form.service.itemText(form.service.currentIndex()) else "",
        template=tpl, name_mode=form.naming.currentData(), folder=form.folder.currentText().strip(),
        free_proxies=form.free if form.fallback.currentData() == "free" else None, defaults=defaults)
    n = len(res["profiles"])
    log(f"Импорт аккаунтов: создано профилей {n}, новых прокси {len(res['new_proxies'])}"
        + (f", без прокси {res['without_proxy']}" if res["without_proxy"] else ""))
    new_proxies = [px for i in res["new_proxies"] if (px := db.get_proxy(i))]
    if new_proxies and settings.get("check_proxy_on_add"):
        from ..proxy.checker import check_proxy
        from .workers import run_bg

        async def check_all():
            import asyncio
            r = await asyncio.gather(*(check_proxy(px) for px in new_proxies), return_exceptions=True)
            for px in new_proxies:
                db.save_proxy(px)
            return sum(1 for x in r if isinstance(x, dict) and x.get("ok"))

        run_bg(lambda: manager.run(check_all(), timeout=300),
               lambda ok, e: log(f"Проверка новых прокси: {e}" if e else
                                 f"Новые прокси проверены: работают {ok} из {len(new_proxies)}"), host)
    return n


# ---------------- мастер первого запуска ----------------
class Onboarding(QFrame):
    """Вместо пустой таблицы: три шага с кнопками и отметками «сделано»."""

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.setObjectName("onboarding")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)
        card = QFrame()
        card.setObjectName("settingRow")
        card.setMaximumWidth(640)
        v = QVBoxLayout(card)
        v.setContentsMargins(28, 24, 28, 24)
        v.setSpacing(14)
        t = QLabel("Начало работы")
        t.setObjectName("settingsH2")
        v.addWidget(t)
        v.addWidget(_hint("Три шага до первого рабочего профиля. Можно пропустить любой и вернуться позже."))
        self.rows = []
        for n, (title, desc, btn, fn) in enumerate((
                ("Добавьте прокси", "Без прокси сайты видят ваш настоящий IP — все профили окажутся на одном адресе.",
                 "Добавить прокси", self._proxies),
                ("Создайте профиль", "Один — кнопкой «+», много сразу — «Создать массово» с прокси по одному на профиль.",
                 "Создать профиль", page.new_profile),
                ("Проверьте отпечаток", "Встроенная проверка: IP, таймзона, WebRTC, следы автоматизации — 10 секунд.",
                 "Проверить", self._check)), 1):
            row = QHBoxLayout()
            row.setSpacing(14)
            mark = QLabel(str(n))
            mark.setFixedSize(28, 28)
            mark.setAlignment(Qt.AlignCenter)
            text = QVBoxLayout()
            text.setSpacing(2)
            tl = QLabel(title)
            tl.setObjectName("settingTitle")
            text.addWidget(tl)
            text.addWidget(_hint(desc))
            b = QPushButton(btn)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            row.addWidget(mark, 0, Qt.AlignTop)
            row.addLayout(text, 1)
            row.addWidget(b, 0, Qt.AlignVCenter)
            v.addLayout(row)
            self.rows.append((mark, b))
        center = QHBoxLayout()
        center.addStretch(1)
        center.addWidget(card, 3)
        center.addStretch(1)
        outer.addLayout(center)
        outer.addStretch(2)

    def _proxies(self):
        win = self.page.win
        win.open_view("proxies")
        win.pages["proxies"].add_proxies()
        self.page.refresh()

    def _check(self):
        profiles = db.list_profiles()
        if not profiles:
            self.page.win.log("Сначала создайте профиль (шаг 2)")
            return
        self.page.table.selectRow(0)
        self.page.check_selected()

    def update_state(self):
        profiles = db.list_profiles()
        done = [bool(db.list_proxies()), bool(profiles), any(p.last_check for p in profiles)]
        nxt = done.index(False) if False in done else -1
        for i, ((mark, b), ok) in enumerate(zip(self.rows, done)):
            mark.setText("✓" if ok else str(i + 1))
            color = C["green"] if ok else (C["accent"] if i == nxt else C["faint"])
            mark.setStyleSheet(f"border: 2px solid {color}; border-radius: 14px; color: {color}; font-weight: 600;")
            b.setObjectName("primary" if i == nxt else "")
            b.style().unpolish(b)
            b.style().polish(b)
