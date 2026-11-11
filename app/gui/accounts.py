"""Пароли и 2FA профиля: список аккаунтов, коды TOTP в реальном времени, копирование в один клик."""
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from .. import db, totp
from ..models import Account
from .icons import icon
from .modal import Modal, confirm
from .theme import C

CLEAR_AFTER = 30  # секунд: потом буфер обмена очищается, если там всё ещё наш секрет
_COLS = ["Сервис", "Логин", "Пароль", "Код 2FA", ""]


def copy_secret(text: str, log=None, what: str = "") -> None:
    cb = QApplication.clipboard()
    cb.setText(text)
    if log:
        log(f"Скопировано: {what}" + (f" (буфер очистится через {CLEAR_AFTER} с)" if what != "логин" else ""))
    if what != "логин":
        QTimer.singleShot(CLEAR_AFTER * 1000, lambda: cb.text() == text and cb.clear())


class AccountForm(QWidget):
    def __init__(self, acc: Account):
        super().__init__()
        self.acc = acc
        self.service = QLineEdit(acc.service)
        self.service.setPlaceholderText("Google, Facebook, Binance…")
        self.url = QLineEdit(acc.url)
        self.url.setPlaceholderText("https://accounts.google.com (необязательно)")
        self.login = QLineEdit(acc.login)
        self.password = QLineEdit(acc.password)
        self.password.setEchoMode(QLineEdit.Password)
        eye = self.password.addAction(icon("eye", C["muted"], 14), QLineEdit.TrailingPosition)
        eye.setToolTip("Показать / скрыть")
        eye.triggered.connect(lambda: self.password.setEchoMode(
            QLineEdit.Normal if self.password.echoMode() == QLineEdit.Password else QLineEdit.Password))
        self.secret = QLineEdit(acc.totp)
        self.secret.setPlaceholderText("Секрет base32 (JBSW Y3DP …) или ссылка otpauth://totp/…")
        self.preview = QLabel()
        self.preview.setObjectName("hint")
        self.notes = QPlainTextEdit(acc.notes)
        self.notes.setPlaceholderText("Резервные коды, ответ на секретный вопрос…")
        self.notes.setFixedHeight(64)
        f = QFormLayout(self)
        f.setContentsMargins(0, 0, 0, 0)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(10)
        for label, w in (("Сервис", self.service), ("Адрес", self.url), ("Логин", self.login),
                         ("Пароль", self.password), ("Секрет 2FA", self.secret), ("", self.preview),
                         ("Заметки", self.notes)):
            lbl = QLabel(label)
            lbl.setObjectName("formLabel")
            f.addRow(lbl, w)
        hint = QLabel("Пароль, секрет 2FA и заметки хранятся зашифрованными (ключ Windows этой учётной записи).")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        f.addRow("", hint)
        self.secret.textChanged.connect(self._preview)
        self._preview()

    def _preview(self):
        s = self.secret.text().strip()
        if not s:
            self.preview.setText("Без 2FA")
            return
        try:
            self.preview.setText(f"Сейчас код: {totp.code(s)} (ещё {totp.remaining(s)} с)")
        except ValueError as e:
            self.preview.setText(f"Ошибка: {e}")

    def validate(self) -> Optional[str]:
        if not (self.service.text().strip() or self.login.text().strip()):
            return "Укажите сервис или логин"
        s = self.secret.text().strip()
        if s and not totp.valid(s):
            try:
                totp.parse(s)
            except ValueError as e:
                return f"Секрет 2FA: {e}"
        return None

    def result(self) -> Account:
        a = self.acc.model_copy()
        a.service = self.service.text().strip()
        a.url = self.url.text().strip()
        a.login = self.login.text().strip()
        a.password = self.password.text()
        a.totp = self.secret.text().strip()
        a.notes = self.notes.toPlainText()
        return a


def edit_account(host, acc: Account) -> Optional[Account]:
    form = AccountForm(acc)
    ok = Modal(host, "Изменить аккаунт" if acc.id else "Новый аккаунт", form,
               ok_text="Сохранить" if acc.id else "Добавить", width=560, validate=form.validate).exec()
    return db.save_account(form.result()) if ok else None


class AccountsView(QWidget):
    """Таблица аккаунтов профиля. Клик по логину, паролю или коду — копирование."""

    def __init__(self, host, pid: int, log=None):
        super().__init__()
        self.host, self.pid, self.log = host, pid, log
        self.accounts: list[Account] = []
        self.show_pw = False
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.table = QTableWidget(0, len(_COLS))
        self.table.setObjectName("boxList")
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setShowGrid(False)
        self.table.setMinimumHeight(220)
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i, w in ((1, 190), (2, 120), (3, 110), (4, 34)):
            self.table.setColumnWidth(i, w)
        self.table.cellClicked.connect(self._clicked)
        self.table.cellDoubleClicked.connect(lambda r, c: self.edit() if c == 0 else None)
        v.addWidget(self.table, 1)
        self.hint = QLabel("Клик по логину, паролю или коду копирует его. Двойной клик по сервису — изменить.")
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)
        row = QHBoxLayout()
        for text, ico, fn in (("  Добавить", "plus", self.add), ("  Изменить", "edit", self.edit),
                              ("  Удалить", "trash", self.delete)):
            b = QPushButton(text)
            b.setIcon(icon(ico, C["text"], 14))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        self.eye = QPushButton("  Показать пароли")
        self.eye.setIcon(icon("eye", C["text"], 14))
        self.eye.setCursor(Qt.PointingHandCursor)
        self.eye.clicked.connect(self._toggle_pw)
        row.addWidget(self.eye)
        v.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        self.reload()

    # ---------- данные ----------
    def reload(self):
        self.accounts = db.list_accounts(self.pid)
        self.table.setRowCount(len(self.accounts))
        for r, a in enumerate(self.accounts):
            svc = QTableWidgetItem(a.service or "—")
            if a.url:
                svc.setToolTip(a.url)
            self.table.setItem(r, 0, svc)
            self.table.setItem(r, 1, QTableWidgetItem(a.login or "—"))
            self.table.setItem(r, 2, QTableWidgetItem(self._pw_text(a)))
            self.table.setItem(r, 3, QTableWidgetItem(""))
            note = QTableWidgetItem("✎" if a.notes else "")
            note.setToolTip(a.notes)
            self.table.setItem(r, 4, note)
            for c in (1, 2):
                self.table.item(r, c).setToolTip("Клик — скопировать")
        self.table.setVisible(True)
        self._tick()

    def _pw_text(self, a: Account) -> str:
        if not a.password:
            return "—"
        return a.password if self.show_pw else "•" * 10

    def _tick(self):
        for r, a in enumerate(self.accounts):
            it = self.table.item(r, 3)
            if not it:
                continue
            if not a.totp:
                it.setText("—")
                continue
            try:
                c, left = totp.code(a.totp), totp.remaining(a.totp)
                it.setText(f"{c[:3]} {c[3:]}   {left:>2}с")
                it.setForeground(QColor(C["red"] if left <= 5 else C["text"]))
                it.setToolTip("Клик — скопировать код")
            except ValueError:
                it.setText("ошибка")

    def _toggle_pw(self):
        self.show_pw = not self.show_pw
        self.eye.setText("  Скрыть пароли" if self.show_pw else "  Показать пароли")
        for r, a in enumerate(self.accounts):
            self.table.item(r, 2).setText(self._pw_text(a))

    def _clicked(self, r: int, c: int):
        a = self.accounts[r]
        if c == 1 and a.login:
            copy_secret(a.login, self.log, "логин")
        elif c == 2 and a.password:
            copy_secret(a.password, self.log, "пароль")
        elif c == 3 and a.totp:
            try:
                copy_secret(totp.code(a.totp), self.log, "код 2FA")
            except ValueError:
                return
        else:
            return
        self.hint.setText(f"Скопировано: {['', 'логин', 'пароль', 'код 2FA'][c]} «{a.service or a.login}»")

    def _current(self) -> Optional[Account]:
        r = self.table.currentRow()
        return self.accounts[r] if 0 <= r < len(self.accounts) else None

    # ---------- действия ----------
    def add(self):
        if edit_account(self.host, Account(profile_id=self.pid)):
            self.reload()

    def edit(self):
        a = self._current()
        if a and edit_account(self.host, a):
            self.reload()

    def delete(self):
        a = self._current()
        if a and confirm(self.host, "Удалить аккаунт?", f"«{a.service or a.login}» будет удалён вместе с паролем и 2FA."):
            db.delete_account(a.id)
            self.reload()


def accounts_modal(host, pid: int, log=None):
    p = db.get_profile(pid)
    view = AccountsView(host, pid, log)
    Modal(host, f"Пароли и 2FA — #{pid} {p.name if p else ''}", view, ok_text="Закрыть", cancel_text=None,
          width=720).exec()
    view.timer.stop()
