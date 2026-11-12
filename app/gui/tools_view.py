"""Окна профилей: журнал активности, план прогрева, уникальность профилей между собой."""
import datetime as dt

from PySide6.QtCore import Qt, QTime
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
                               QPushButton, QTimeEdit, QVBoxLayout, QWidget)

from .. import activity, db, uniqueness, warmplan
from .icons import icon
from .modal import Modal
from .theme import C
from .workers import run_bg

_KIND_ICON = {"start": "play", "stop": "stop", "scenario": "workflow", "warmup": "flame", "plan": "flame",
              "check": "fingerprint", "proxy": "globe", "ban": "tag", "edit": "edit"}


def _hint(text: str) -> QLabel:
    h = QLabel(text)
    h.setObjectName("hint")
    h.setWordWrap(True)
    return h


# ---------------- журнал активности ----------------
class ActivityView(QWidget):
    def __init__(self, pid: int):
        super().__init__()
        self.pid = pid
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        row = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.addItem("Все события", None)
        for k, t in activity.KINDS.items():
            self.kind.addItem(icon(_KIND_ICON.get(k, "check"), C["muted"], 14), t, k)
        self.period = QComboBox()
        for label, days in (("7 дней", 7), ("30 дней", 30), ("180 дней", 180)):
            self.period.addItem(label, days)
        self.period.setCurrentIndex(1)
        for cb in (self.kind, self.period):
            cb.currentIndexChanged.connect(lambda _: self.reload())
            row.addWidget(cb)
        row.addStretch()
        v.addLayout(row)
        self.summary = _hint("")
        v.addWidget(self.summary)
        self.list = QListWidget()
        self.list.setObjectName("boxList")
        self.list.setMinimumHeight(360)
        v.addWidget(self.list)
        v.addWidget(_hint("Записываются запуски (с окном / без, прокси), остановки — сколько профиль работал и на каких "
                          "сайтах был, сценарии, прогрев, проверки, смены IP и страны прокси, метки. "
                          "Наведите на остановку — полный список сайтов."))
        self.reload()

    def reload(self):
        import time
        since = time.time() - self.period.currentData() * 86400
        rows = db.list_activity(self.pid, since=since, kind=self.kind.currentData(), limit=1000)
        self.list.clear()
        total = sum((r["data"] or {}).get("seconds", 0) for r in rows if r["kind"] == "stop")
        starts = sum(1 for r in rows if r["kind"] == "start")
        self.summary.setText(f"Событий: {len(rows)} · запусков: {starts} · в работе: {activity.duration(total)}")
        for r in rows:
            when = dt.datetime.fromtimestamp(r["ts"]).strftime("%d.%m %H:%M")
            it = QListWidgetItem(icon(_KIND_ICON.get(r["kind"], "check"), C["muted"], 14),
                                 f"{when}   {activity.KINDS.get(r['kind'], r['kind'])}:  {r['text']}")
            hosts = (r["data"] or {}).get("hosts")
            if hosts:
                it.setToolTip("\n".join(f"{h} — переходов: {n}" for h, n in hosts.items()))
            if r["kind"] == "ban" or "ошибка" in r["text"] or "не запущен" in r["text"] or "не отвечает" in r["text"]:
                it.setForeground(QColor(C["red"]))
            self.list.addItem(it)
        if not rows:
            self.list.addItem("За этот период событий нет")


def activity_modal(host, pid: int):
    p = db.get_profile(pid)
    Modal(host, f"Журнал активности — #{pid} {p.name if p else ''}", ActivityView(pid), ok_text="Закрыть",
          cancel_text=None, width=820).exec()


# ---------------- план прогрева ----------------
class PlanForm(QWidget):
    def __init__(self, pids: list[int]):
        super().__init__()
        self.pids = pids
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        cur = {(db.get_profile(i).warm_plan or {}).get("plan", "") for i in pids if db.get_profile(i)}
        self.plan = QComboBox()
        self.plan.addItem("Без плана (снять)", "")
        for k, pl in warmplan.PLANS.items():
            self.plan.addItem(pl["title"], k)
        self.plan.setCurrentIndex(max(0, self.plan.findData(next(iter(cur)) if len(cur) == 1 and "" not in cur
                                                           else "generic")))
        v.addWidget(self.plan)
        self.days = QPlainTextEdit()
        self.days.setReadOnly(True)
        self.days.setFixedHeight(170)
        v.addWidget(self.days)
        self.task = QCheckBox("Выполнять каждый день в")
        self.task.setChecked(True)
        self.time = QTimeEdit(QTime(10, 0))
        self.time.setDisplayFormat("HH:mm")
        row = QHBoxLayout()
        row.addWidget(self.task)
        row.addWidget(self.time)
        row.addStretch()
        v.addLayout(row)
        self.now = QCheckBox("Выполнить первый день сейчас (без окна)")
        v.addWidget(self.now)
        no_proxy = [i for i in pids if (p := db.get_profile(i)) and not p.proxy_id]
        if no_proxy:
            w = QLabel(f"⚠ Без прокси: {', '.join(f'#{i}' for i in no_proxy[:10])}"
                       f"{'…' if len(no_proxy) > 10 else ''} — прогрев пойдёт с вашего настоящего IP, и площадка "
                       "запомнит его вместе с cookies. Назначьте прокси до прогрева.")
            w.setObjectName("modalError")
            w.setWordWrap(True)
            v.addWidget(w)
        state = [f"#{i} {p.name}: {warmplan.progress(p) or 'без плана'}" for i in pids[:8] if (p := db.get_profile(i))]
        v.addWidget(_hint(f"Профилей: {len(pids)}. Сейчас:\n" + "\n".join(state) +
                          ("\n…" if len(pids) > 8 else "") +
                          "\n\nДень плана выполняется не чаще раза в сутки; пропущенный день сдвигает план. "
                          "Задача создаётся в Планировщике (работает, пока открыт claudeAnty). Вход в аккаунты "
                          "не нужен: профиль копит историю и cookies, в том числе на самой площадке."))
        self.plan.currentIndexChanged.connect(lambda _: self._show())
        self._show()

    def _show(self):
        pl = warmplan.PLANS.get(self.plan.currentData())
        self.task.setEnabled(bool(pl))
        self.now.setEnabled(bool(pl))
        if not pl:
            self.days.setPlainText("План будет снят с профилей.")
            return
        lines = []
        for i, d in enumerate(pl["days"], 1):
            special = [s.replace("https://", "").replace("www.", "") for s in d["sites"]
                       if s not in warmplan.COMMON + warmplan.NEWS + warmplan.SHOPS]
            lines.append(f"День {i}: сайтов {d['count']} по ~{d['dwell']} с, поисков в Google {d['searches']}"
                         + (f"; площадка: {', '.join(special)}" if special else "; только общие сайты"))
        self.days.setPlainText("\n".join(lines))


def plan_modal(host, pids: list[int], log, manager):
    form = PlanForm(pids)
    if not Modal(host, "План прогрева", form, ok_text="Применить", width=640).exec():
        return
    key = form.plan.currentData()
    for pid in pids:
        warmplan.assign(pid, key)
    if not key:
        log(f"План прогрева снят — профилей: {len(pids)}")
        return
    title = warmplan.PLANS[key]["title"]
    log(f"План «{title}» назначен — профилей: {len(pids)}")
    if form.task.isChecked():
        from ..scheduler import next_run
        import time
        t = {"name": f"План «{title}»", "enabled": True, "action": "warm_plan", "headless": True,
             "targets": {"mode": "ids", "ids": pids},
             "schedule": {"kind": "daily", "time": form.time.time().toString("HH:mm"), "days": list(range(7))}}
        t["next_run"] = next_run(t, time.time())
        db.save_doc("tasks", t)
        log(f"Задача Планировщика «{t['name']}» — каждый день в {t['schedule']['time']}")
    if form.now.isChecked():
        async def go():
            import asyncio
            return await asyncio.gather(*(warmplan.run_day(manager, pid, log=manager.scheduler.say) for pid in pids),
                                        return_exceptions=True)
        run_bg(lambda: manager.run(go(), timeout=None),
               lambda r, e: log(f"План прогрева: {e}" if e else
                                f"День плана выполнен: {sum(1 for x in r if isinstance(x, dict) and x.get('day'))} "
                                f"из {len(pids)}"), host)


# ---------------- уникальность ----------------
class UniquenessView(QWidget):
    def __init__(self, pids: list[int], log, manager):
        super().__init__()
        self.pids, self.log, self.m = pids, log, manager
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.scope = QComboBox()
        if len(pids) > 1:
            self.scope.addItem(f"Выбранные профили ({len(pids)})", pids)
        self.scope.addItem("Все профили", None)
        self.scope.currentIndexChanged.connect(lambda _: self.reload())
        self.recollect = QPushButton("  Снять отпечатки заново")
        self.recollect.setIcon(icon("refresh", C["text"], 14))
        self.recollect.setCursor(Qt.PointingHandCursor)
        self.recollect.clicked.connect(self.collect)
        row = QHBoxLayout()
        row.addWidget(self.scope)
        row.addStretch()
        row.addWidget(self.recollect)
        v.addLayout(row)
        self.text = QPlainTextEdit()
        self.text.setObjectName("console")
        self.text.setReadOnly(True)
        self.text.setMinimumHeight(340)
        v.addWidget(self.text)
        v.addWidget(_hint("Профили одного ПК рисуют canvas, WebGL и audio одним железом — уникальными их делает "
                          "шум от seed профиля. Совпадение хэшей (✗) значит, что сайт может связать аккаунты. "
                          "«Снять заново» запускает каждый профиль без окна на несколько секунд."))
        self.reload()

    def _ids(self):
        ids = self.scope.currentData()
        return ids if ids is not None else [p.id for p in db.list_profiles()]

    def reload(self):
        self.text.setPlainText(uniqueness.format_report(uniqueness.analyze(self._ids())))

    def collect(self):
        ids = self._ids()
        self.recollect.setEnabled(False)
        self.text.setPlainText(f"Снимаю отпечатки у профилей: {len(ids)}…")

        def done(n, e):
            self.recollect.setEnabled(True)
            self.log(f"Уникальность: ошибка — {e}" if e else f"Уникальность: отпечатки сняты у {n} из {len(ids)}")
            self.reload()

        run_bg(lambda: self.m.run(uniqueness.collect(self.m, ids, self.m.scheduler.say), timeout=None), done, self)


def uniqueness_modal(host, pids: list[int], log, manager):
    Modal(host, "Уникальность профилей", UniquenessView(pids, log, manager), ok_text="Закрыть", cancel_text=None,
          width=820).exec()


# ---------------- статистика банов ----------------
class BanStatsView(QWidget):
    _COLS = ["Группа", "Профилей", "Забанено", "Доля", "Сейчас в бане", "Жили до бана", "Последний бан"]

    def __init__(self):
        super().__init__()
        from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget
        from .. import banstats
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        row = QHBoxLayout()
        self.dim = QComboBox()
        for k, t in banstats.DIMENSIONS.items():
            self.dim.addItem(f"По: {t.lower()}", k)
        self.period = QComboBox()
        for label, days in (("За всё время", 0), ("30 дней", 30), ("90 дней", 90), ("180 дней", 180)):
            self.period.addItem(label, days)
        for cb in (self.dim, self.period):
            cb.currentIndexChanged.connect(lambda _: self.reload())
            row.addWidget(cb)
        row.addStretch()
        v.addLayout(row)
        self.summary = _hint("")
        v.addWidget(self.summary)
        self.table = QTableWidget(0, len(self._COLS))
        self.table.setObjectName("boxList")
        self.table.setHorizontalHeaderLabels(self._COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setShowGrid(False)
        self.table.setMinimumHeight(320)
        hh = self.table.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i, w in enumerate((0, 80, 80, 70, 100, 100, 110)):
            if w:
                self.table.setColumnWidth(i, w)
        v.addWidget(self.table, 1)
        v.addWidget(_hint("Доля — сколько профилей группы получили «Бан» (вручную, автоматически по признакам на "
                          "странице или через API). Забаненный профиль учитывается в той группе, где он был в момент "
                          "бана, даже если его потом перенесли или удалили. Провайдер — поле «Провайдер» прокси или "
                          "домен его хоста. Мало профилей в группе — мало смысла в доле: смотрите и на количество."))
        self.reload()

    def reload(self):
        from PySide6.QtWidgets import QTableWidgetItem
        from .. import banstats
        s = banstats.stats(self.dim.currentData(), self.period.currentData())
        self.summary.setText(f"Профилей сейчас: {s['total_profiles']}. Банов за период: {s['total_bans']} "
                             f"(профилей: {s['banned_profiles']}). Сверху — группы с самой большой долей банов.")
        rows = s["rows"]
        self.table.setRowCount(len(rows))
        worst = max((r["rate"] for r in rows if r["profiles"] >= 3), default=0)
        for i, r in enumerate(rows):
            life = f"{r['avg_life_days']} дн." if r["avg_life_days"] is not None else "—"
            vals = [r["group"], str(r["profiles"]), str(r["banned_profiles"]), f"{r['rate'] * 100:.0f}%",
                    str(r["active_bans"]), life, r["last_ban"] or "—"]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if c == 3 and r["rate"] > 0:
                    it.setForeground(QColor(C["red"] if r["rate"] >= worst and r["profiles"] >= 3 else C["text"]))
                self.table.setItem(i, c, it)


def banstats_modal(host):
    Modal(host, "Статистика банов", BanStatsView(), ok_text="Закрыть", cancel_text=None, width=860).exec()


# ---------------- расстановка окон ----------------
def arrange_modal(host, pids: list[int]):
    from PySide6.QtWidgets import QFormLayout, QSpinBox
    from .. import settings, winlayout
    if not winlayout.SUPPORTED:
        host.log("Расстановка окон работает только в Windows")
        return
    skip = set(getattr(host, "browser_tabs", {}))
    pids = [p for p in pids if p not in skip]
    if not pids:
        host.log("Нет запущенных профилей с отдельным окном — запустите профили (встроенные во вкладки не раскладываются)")
        return
    w = QWidget()
    f = QFormLayout(w)
    f.setContentsMargins(0, 0, 0, 0)
    f.setHorizontalSpacing(16)
    f.setVerticalSpacing(10)
    mode = QComboBox()
    mode.addItem("Сетка — окна рядом, без перекрытия", "grid")
    mode.addItem("Каскад — окна лесенкой", "cascade")
    mon = QComboBox()
    for m in winlayout.monitors():
        mon.addItem(f"Монитор {m['index'] + 1}{' (основной)' if m['primary'] else ''} — {m['width']}×{m['height']}",
                    m["index"])
    cols = QSpinBox()
    cols.setRange(0, 12)
    cols.setSpecialValueText("авто")
    gap = QSpinBox()
    gap.setRange(0, 40)
    gap.setSuffix(" px")
    last = settings.get("arrange_last") or {}
    mode.setCurrentIndex(max(0, mode.findData(last.get("mode", "grid"))))
    mon.setCurrentIndex(max(0, mon.findData(last.get("monitor", 0))))
    cols.setValue(int(last.get("cols", 0)))
    gap.setValue(int(last.get("gap", 0)))
    info = _hint("")

    def upd(*_):
        lst = winlayout.monitors()
        m = lst[mon.currentData()] if mon.currentData() is not None and mon.currentData() < len(lst) else lst[0]
        if mode.currentData() == "grid":
            c, r = winlayout.grid(len(pids), m["width"], m["height"], cols.value())
            cw, ch = (m["width"] - gap.value() * (c - 1)) // c, (m["height"] - gap.value() * (r - 1)) // r
            small = " Chrome не делает окна уже ~500 px — они частично перекроются." if cw < 500 else ""
            info.setText(f"Окон: {len(pids)} → {c}×{r}, ячейка {cw}×{ch} px.{small}")
        else:
            info.setText(f"Окон: {len(pids)} лесенкой со сдвигом 32 px.")

    for sig in (mode.currentIndexChanged, mon.currentIndexChanged, cols.valueChanged, gap.valueChanged):
        sig.connect(upd)
    f.addRow(QLabel("Как"), mode)
    f.addRow(QLabel("Где"), mon)
    f.addRow(QLabel("Колонок"), cols)
    f.addRow(QLabel("Зазор"), gap)
    f.addRow("", info)
    f.addRow("", _hint("Порядок — как в таблице (или выделенные). Окно ПК-профиля не становится больше его экрана "
                       "из отпечатка, телефон — шире устройства. Браузеру ничего не отправляется: для сайта это обычное "
                       "изменение размера окна."))
    upd()
    if not Modal(host, "Разложить окна", w, ok_text="Разложить", width=560).exec():
        return
    opts = {"mode": mode.currentData(), "monitor": mon.currentData() or 0, "cols": cols.value(), "gap": gap.value()}
    settings.set("arrange_last", opts)
    run_bg(lambda: winlayout.arrange(pids, skip=skip, **opts),
           lambda r, e: host.log(f"Расстановка окон: {e}" if e else
                                 f"Окна разложены: {len(r['placed'])}"
                                 + (f" ({r['cols']}×{r['rows']})" if r["cols"] else "")
                                 + (f", не найдено окно у #{', #'.join(map(str, r['missing']))}" if r["missing"] else "")),
           host)
