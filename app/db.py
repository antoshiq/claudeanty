import json
import sqlite3
import threading
from typing import Optional

from .config import DB_PATH
from . import crypto
from .models import Account, Profile, Proxy

_lock = threading.RLock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript("""
CREATE TABLE IF NOT EXISTS proxies(
  id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS profiles(
  id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id INTEGER NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS accounts_profile ON accounts(profile_id);
CREATE TABLE IF NOT EXISTS scenarios(
  id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS health(
  id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id INTEGER NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL,
  ok INTEGER NOT NULL, warn INTEGER NOT NULL, fail INTEGER NOT NULL, items TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS health_profile ON health(profile_id, ts);
""")
_conn.commit()


def _save(table: str, model, ident: Optional[int]):
    with _lock:
        if ident is None:
            cur = _conn.execute(f"INSERT INTO {table}(data) VALUES(?)", ("{}",))
            ident = cur.lastrowid
        model.id = ident
        _conn.execute(f"UPDATE {table} SET data=? WHERE id=?", (model.model_dump_json(), ident))
        _conn.commit()
        return model


# --- proxies ---
def save_proxy(p: Proxy) -> Proxy:
    return _save("proxies", p, p.id)


def get_proxy(pid: Optional[int]) -> Optional[Proxy]:
    if pid is None:
        return None
    with _lock:
        r = _conn.execute("SELECT data FROM proxies WHERE id=?", (pid,)).fetchone()
    return Proxy(**json.loads(r["data"])) if r else None


def list_proxies() -> list[Proxy]:
    with _lock:
        rows = _conn.execute("SELECT data FROM proxies ORDER BY id").fetchall()
    return [Proxy(**json.loads(r["data"])) for r in rows]


def delete_proxy(pid: int):
    with _lock:
        _conn.execute("DELETE FROM proxies WHERE id=?", (pid,))
        _conn.commit()


# --- profiles ---
def save_profile(p: Profile) -> Profile:
    return _save("profiles", p, p.id)


def get_profile(pid: int) -> Optional[Profile]:
    with _lock:
        r = _conn.execute("SELECT data FROM profiles WHERE id=?", (pid,)).fetchone()
    return Profile(**json.loads(r["data"])) if r else None


def list_profiles() -> list[Profile]:
    with _lock:
        rows = _conn.execute("SELECT data FROM profiles ORDER BY id").fetchall()
    return [Profile(**json.loads(r["data"])) for r in rows]


def next_number(base: str) -> int:
    """Следующий свободный номер для названий «base N»."""
    import re
    rx = re.compile(rf"^{re.escape(base)}\s*(\d+)$")
    nums = [int(m.group(1)) for p in list_profiles() if (m := rx.match(p.name.strip()))]
    return max(nums, default=0) + 1


def delete_profile(pid: int):
    with _lock:
        _conn.execute("DELETE FROM profiles WHERE id=?", (pid,))
        _conn.execute("DELETE FROM accounts WHERE profile_id=?", (pid,))
        _conn.execute("DELETE FROM health WHERE profile_id=?", (pid,))
        _conn.execute("DELETE FROM activity WHERE profile_id=?", (pid,))
        _conn.commit()


# --- аккаунты (пароли и 2FA): секретные поля шифруются ---
_SECRET = ("password", "totp", "notes")


def save_account(a: Account) -> Account:
    data = a.model_dump()
    for k in _SECRET:
        data[k] = crypto.encrypt(data[k]) if data[k] else ""
    with _lock:
        if a.id is None:
            a.id = _conn.execute("INSERT INTO accounts(profile_id, data) VALUES(?, '{}')", (a.profile_id,)).lastrowid
        data["id"] = a.id
        _conn.execute("UPDATE accounts SET data=?, profile_id=? WHERE id=?", (json.dumps(data, ensure_ascii=False),
                                                                             a.profile_id, a.id))
        _conn.commit()
    return a


def _account(row) -> Account:
    data = json.loads(row["data"])
    data["id"] = row["id"]
    for k in _SECRET:
        if data.get(k):
            try:
                data[k] = crypto.decrypt(data[k])
            except Exception:  # noqa: BLE001 — база с другого ПК / другой учётной записи Windows
                data[k] = ""
                data.setdefault("unreadable", True)
    data.pop("unreadable", None)
    return Account(**data)


def list_accounts(profile_id: Optional[int] = None) -> list[Account]:
    with _lock:
        if profile_id is None:
            rows = _conn.execute("SELECT id, data FROM accounts ORDER BY id").fetchall()
        else:
            rows = _conn.execute("SELECT id, data FROM accounts WHERE profile_id=? ORDER BY id", (profile_id,)).fetchall()
    return [_account(r) for r in rows]


def get_account(aid: int) -> Optional[Account]:
    with _lock:
        r = _conn.execute("SELECT id, data FROM accounts WHERE id=?", (aid,)).fetchone()
    return _account(r) if r else None


def delete_account(aid: int):
    with _lock:
        _conn.execute("DELETE FROM accounts WHERE id=?", (aid,))
        _conn.commit()


def count_accounts() -> dict[int, int]:
    with _lock:
        rows = _conn.execute("SELECT profile_id, COUNT(*) AS n FROM accounts GROUP BY profile_id").fetchall()
    return {r["profile_id"]: r["n"] for r in rows}


# --- сценарии и задачи планировщика: простые JSON-документы ---
def save_doc(table: str, data: dict) -> dict:
    assert table in ("scenarios", "tasks")
    with _lock:
        if data.get("id") is None:
            data["id"] = _conn.execute(f"INSERT INTO {table}(data) VALUES('{{}}')").lastrowid
        _conn.execute(f"UPDATE {table} SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), data["id"]))
        _conn.commit()
    return data


def list_docs(table: str) -> list[dict]:
    assert table in ("scenarios", "tasks")
    with _lock:
        rows = _conn.execute(f"SELECT id, data FROM {table} ORDER BY id").fetchall()
    return [{**json.loads(r["data"]), "id": r["id"]} for r in rows]


def get_doc(table: str, ident: int) -> Optional[dict]:
    assert table in ("scenarios", "tasks")
    with _lock:
        r = _conn.execute(f"SELECT id, data FROM {table} WHERE id=?", (ident,)).fetchone()
    return {**json.loads(r["data"]), "id": r["id"]} if r else None


def delete_doc(table: str, ident: int):
    assert table in ("scenarios", "tasks")
    with _lock:
        _conn.execute(f"DELETE FROM {table} WHERE id=?", (ident,))
        _conn.commit()


# --- здоровье профилей: история проверок и отчётов детекторов ---
def add_health(pid: int, ts: float, kind: str, ok: int, warn: int, fail: int, items: list) -> None:
    with _lock:
        _conn.execute("INSERT INTO health(profile_id, ts, kind, ok, warn, fail, items) VALUES(?,?,?,?,?,?,?)",
                      (pid, ts, kind, ok, warn, fail, json.dumps(items, ensure_ascii=False)))
        _conn.commit()


def list_health(pid: Optional[int] = None, since: float = 0, kind: Optional[str] = None) -> list[dict]:
    """Записи по возрастанию времени: {"profile_id", "ts", "kind", "ok", "warn", "fail", "items"}."""
    sql, args = "SELECT profile_id, ts, kind, ok, warn, fail, items FROM health WHERE ts >= ?", [since]
    if pid is not None:
        sql += " AND profile_id = ?"
        args.append(pid)
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    with _lock:
        rows = _conn.execute(sql + " ORDER BY ts, id", args).fetchall()
    return [{**dict(r), "items": json.loads(r["items"])} for r in rows]


def health_profiles() -> set[int]:
    with _lock:
        return {r[0] for r in _conn.execute("SELECT DISTINCT profile_id FROM health").fetchall()}


# --- журнал активности профилей ---
_conn.executescript("""
CREATE TABLE IF NOT EXISTS activity(
  id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id INTEGER NOT NULL, ts REAL NOT NULL, kind TEXT NOT NULL,
  text TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS activity_profile ON activity(profile_id, ts);
""")


def add_activity(pid: int, ts: float, kind: str, text: str, data: dict) -> None:
    with _lock:
        _conn.execute("INSERT INTO activity(profile_id, ts, kind, text, data) VALUES(?,?,?,?,?)",
                      (pid, ts, kind, text, json.dumps(data, ensure_ascii=False)))
        _conn.commit()


def list_activity(pid: Optional[int] = None, since: float = 0, kind: Optional[str] = None,
                  limit: int = 500) -> list[dict]:
    """Записи журнала, новые первыми."""
    sql, args = "SELECT id, profile_id, ts, kind, text, data FROM activity WHERE ts >= ?", [since]
    if pid is not None:
        sql += " AND profile_id = ?"
        args.append(pid)
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    with _lock:
        rows = _conn.execute(sql + " ORDER BY ts DESC, id DESC LIMIT ?", [*args, limit]).fetchall()
    return [{**dict(r), "data": json.loads(r["data"])} for r in rows]


def cleanup_activity(before: float) -> None:
    with _lock:
        _conn.execute("DELETE FROM activity WHERE ts < ?", (before,))
        _conn.commit()
