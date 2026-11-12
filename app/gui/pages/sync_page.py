from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout, QWidget)

from ... import db
from ...manager import manager
from ..icons import icon
from ..widgets import set_icon, side_title
from ..workers import run_bg


class SyncSidebar(QFrame):
    def __init__(self, page):
        super().__init__()
        self.setObjectName("sideBar")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(side_title("СИНХРОНИЗАТОР"))
        hint = QLabel(
            "Действия в ведущем профиле повторяются во всех ведомых: клики, ввод с клавиатуры и прокрутка.\n\n"
            "Координаты пересчитываются под размер окна, поэтому ведомым может быть и телефон. "
            "Если страницы у профилей отличаются (другой язык, другая вёрстка), клик может попасть не туда.\n\n"
            "Адрес, набранный в строке адреса ведущего, не повторяется — используйте поле «Открыть во всех».")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        hint.setContentsMargins(18, 4, 14, 0)
        hint.setAlignment(Qt.AlignTop)
        v.addWidget(hint)
        v.addStretch()


class SyncPage(QWidget):
    key, title, icon_name = "sync", "Синхронизатор", "repeat"
    search_hint = "Поиск профилей для синхронизации"

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.sidebar = SyncSidebar(self)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 18, 24, 16)
        v.setSpacing(10)

        self.state = QLabel()
        self.state.setObjectName("settingsH2")
        v.addWidget(self.state)

        row = QHBoxLayout()
        lbl = QLabel("Ведущий профиль")
        lbl.setObjectName("formLabel")
        self.leader = QComboBox()
        self.leader.setMinimumWidth(320)
        self.leader.currentIndexChanged.connect(lambda _: self._fill_followers())
        row.addWidget(lbl)
        row.addWidget(self.leader)
        row.addStretch()
        v.addLayout(row)

        lbl2 = QLabel("Ведомые профили (не запущенные запустятся автоматически)")
        lbl2.setObjectName("formLabel")
        v.addWidget(lbl2)
        self.followers = QListWidget()
        self.followers.setObjectName("boxList")
        v.addWidget(self.followers, 1)

        btns = QHBoxLayout()
        all_btn = QPushButton("Отметить все")
        all_btn.clicked.connect(lambda: self._check_all(True))
        none_btn = QPushButton("Снять все")
        none_btn.clicked.connect(lambda: self._check_all(False))
        self.stop_btn = QPushButton("  Остановить")
        set_icon(self.stop_btn, "stop", "text", 12)
        self.stop_btn.clicked.connect(self.stop)
        self.start_btn = QPushButton("  Начать синхронизацию")
        self.start_btn.setObjectName("primary")
        self.start_btn.setIcon(icon("play", "#ffffff", 12))
        self.start_btn.clicked.connect(self.start)
        for b in (all_btn, none_btn, self.stop_btn, self.start_btn):
            b.setCursor(Qt.PointingHandCursor)
        self.arrange_btn = QPushButton("  Разложить окна")
        set_icon(self.arrange_btn, "grid", "text", 14)
        self.arrange_btn.setToolTip("Ведущий — в левый верхний угол, ведомые — сеткой по экрану")
        self.arrange_btn.clicked.connect(self.arrange)
        self.arrange_btn.setCursor(Qt.PointingHandCursor)
        from PySide6.QtWidgets import QCheckBox
        self.auto_arrange = QCheckBox("Раскладывать при запуске")
        self.auto_arrange.setChecked(True)
        btns.addWidget(all_btn)
        btns.addWidget(none_btn)
        btns.addWidget(self.arrange_btn)
        btns.addWidget(self.auto_arrange)
        btns.addStretch()
        btns.addWidget(self.stop_btn)
        btns.addWidget(self.start_btn)
        v.addLayout(btns)

        url_row = QHBoxLayout()
        self.url = QLineEdit()
        self.url.setPlaceholderText("Адрес, который нужно открыть во всех синхронизируемых профилях")
        self.url.returnPressed.connect(self.open_url)
        go = QPushButton("  Открыть во всех")
        set_icon(go, "external", "text", 14)
        go.clicked.connect(self.open_url)
        go.setCursor(Qt.PointingHandCursor)
        url_row.addWidget(self.url, 1)
        url_row.addWidget(go)
        v.addLayout(url_row)
        self._last_ids = None

    def _check_all(self, on: bool):
        for i in range(self.followers.count()):
            self.followers.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _fill_followers(self):
        checked = {self.followers.item(i).data(Qt.UserRole) for i in range(self.followers.count())
                   if self.followers.item(i).checkState() == Qt.Checked}
        lead = self.leader.currentData()
        q = self.win.search.text().lower().strip()
        self.followers.clear()
        for p in db.list_profiles():
            if p.id == lead or (q and q not in p.name.lower()):
                continue
            mark = "●" if manager.is_running(p.id) else "○"
            it = QListWidgetItem(f"{mark}  #{p.id}  {p.name}" + ("   · телефон" if p.kind == "mobile" else ""))
            it.setData(Qt.UserRole, p.id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            on = p.id in checked or (manager.sync.active and p.id in manager.sync.followers)
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
            self.followers.addItem(it)

    def refresh(self):
        profiles = db.list_profiles()
        ids = [(p.id, p.name, manager.is_running(p.id)) for p in profiles]
        if ids != self._last_ids:  # не перестраиваем список без изменений, чтобы не сбивать отметки
            self._last_ids = ids
            cur = self.leader.currentData()
            if manager.sync.active:
                cur = manager.sync.leader
            self.leader.blockSignals(True)
            self.leader.clear()
            for p in profiles:
                self.leader.addItem(f"{'●' if manager.is_running(p.id) else '○'}  #{p.id}  {p.name}", p.id)
            self.leader.setCurrentIndex(max(0, self.leader.findData(cur)))
            self.leader.blockSignals(False)
            self._fill_followers()
        st = manager.sync.status()
        if st["active"]:
            lead = db.get_profile(st["leader"])
            self.state.setText(f"Синхронизация включена · ведущий «{lead.name if lead else st['leader']}» · "
                               f"ведомых: {len(st['followers'])} · повторено действий: {st['replayed']}"
                               + (f" · ошибок: {st['errors']}" if st["errors"] else ""))
        else:
            self.state.setText("Синхронизация выключена")
        self.start_btn.setEnabled(not st["active"])
        self.stop_btn.setEnabled(st["active"])

    def _selected(self) -> list[int]:
        return [self.followers.item(i).data(Qt.UserRole) for i in range(self.followers.count())
                if self.followers.item(i).checkState() == Qt.Checked]

    def start(self):
        lead, fol = self.leader.currentData(), self._selected()
        if lead is None or not fol:
            self.win.log("Выберите ведущий профиль и хотя бы один ведомый")
            return
        self.win.log(f"Запуск синхронизации: ведущий #{lead}, ведомых {len(fol)}…")
        self.start_btn.setEnabled(False)

        def done(res, err):
            self.win.log(f"Ошибка синхронизации: {err}" if err else "Синхронизация включена — действуйте в ведущем профиле")
            self._last_ids = None
            self.refresh()
            if not err and self.auto_arrange.isChecked():
                self.arrange(wait=10)

        run_bg(lambda: manager.run(manager.sync.start(lead, fol), timeout=300), done, self)

    def arrange(self, wait: float = 0):
        from ...winlayout import SUPPORTED, arrange
        st = manager.sync.status()
        pids = [st["leader"], *st["followers"]] if st["active"] else [self.leader.currentData(), *self._selected()]
        pids = [p for p in pids if p is not None and manager.is_running(p)]
        if not SUPPORTED or not pids:
            self.win.log("Нет запущенных профилей для расстановки" if SUPPORTED else "Расстановка окон — только в Windows")
            return
        skip = set(getattr(self.win, "browser_tabs", {}))
        run_bg(lambda: arrange(pids, skip=skip, wait=wait),
               lambda r, e: self.win.log(f"Расстановка окон: {e}" if e else
                                         f"Окна разложены: {len(r['placed'])} ({r['cols']}×{r['rows']})"
                                         + (f", без окна: {len(r['missing'])}" if r["missing"] else "")), self)

    def stop(self):
        run_bg(lambda: manager.run(manager.sync.stop()),
               lambda r, e: (self.win.log("Синхронизация остановлена"), self.refresh()), self)

    def open_url(self):
        url = self.url.text().strip()
        if not url:
            return
        st = manager.sync.status()
        pids = ([st["leader"], *st["followers"]] if st["active"]
                else [self.leader.currentData(), *self._selected()])
        pids = [p for p in pids if p is not None and manager.is_running(p)]
        if not pids:
            self.win.log("Нет запущенных профилей — сначала начните синхронизацию")
            return
        self.win.log(f"Открываю {url} в профилях: {len(pids)}")
        run_bg(lambda: manager.run(manager.sync.open_url(url, pids)), lambda r, e: None, self)
