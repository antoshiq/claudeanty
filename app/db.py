import json
import sqlite3
import threading
from typing import Optional

from .config import DB_PATH
from .models import Profile, Proxy

_lock = threading.RLock()
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.executescript("""
CREATE TABLE IF NOT EXISTS proxies(
  id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS profiles(
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
        _conn.commit()
