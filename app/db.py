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


def delete_profile(pid: int):
    with _lock:
        _conn.execute("DELETE FROM profiles WHERE id=?", (pid,))
        _conn.execute("DELETE FROM accounts WHERE profile_id=?", (pid,))
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
