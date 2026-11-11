"""Импорт профилей из Dolphin Anty, AdsPower и таблиц (CSV / XLSX / JSON).

Что переносится: название, ОС и тип (ПК/телефон), прокси, cookies, логин/пароль/2FA (в «Пароли и 2FA»),
заметки, теги, папка (группа). Отпечаток НЕ переносится — генерируется свой, согласованный: чужой
User-Agent со своей версией Chrome не совпал бы с реальным движком, а это заметнее, чем новый отпечаток.

Форматы ответов API и шаблонов у сервисов меняются, поэтому разбор устойчивый: поля ищутся по разным
вариантам названий (в любом месте вложенного ответа), колонки таблиц — по синонимам без учёта регистра.
"""
import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from . import db
from .models import Account, Profile, Proxy

# ---------------- поиск полей ----------------
_SYN = {
    "name": ["name", "profile_name", "profile name", "title", "browser_name", "acc_name", "account name"],
    "notes": ["notes", "note", "remark", "remarks", "comment", "description", "content"],
    "folder": ["group", "group_name", "folder", "group name", "category"],
    "tags": ["tags", "tag", "labels"],
    "platform": ["platform", "os", "system", "operating system"],
    "ua": ["ua", "useragent", "user_agent", "user agent", "user-agent"],
    "cookies": ["cookie", "cookies"],
    "proxy": ["proxy", "proxy_string", "proxy string", "proxy_url"],
    "proxy_type": ["proxytype", "proxy_type", "proxy type", "type"],
    "proxy_host": ["proxy_host", "host", "ip", "proxy ip", "proxy_ip", "address", "server"],
    "proxy_port": ["proxy_port", "port"],
    "proxy_user": ["proxy_user", "proxy_login", "login", "user", "proxy username"],
    "proxy_pass": ["proxy_password", "proxy_pass", "pass", "proxy password"],
    "change_url": ["change_ip_url", "changeiplink", "change ip url", "refresh_url", "proxy_refresh_url", "rotate_url"],
    "acc_login": ["username", "account", "account_login", "email", "acc_login"],
    "acc_pass": ["password", "account_password", "acc_password"],
    "totp": ["fakey", "2fa", "totp", "2fa_key", "2fa key", "otp", "secret", "two_factor"],
    "service": ["domain_name", "domain", "platform_name", "site", "service", "website"],
}


def _norm(k: str) -> str:
    return re.sub(r"[\s_\-]+", "_", str(k).strip().lower())


_SYN_N = {key: [_norm(s) for s in names] for key, names in _SYN.items()}


def _pick(d: dict, key: str) -> Any:
    """Значение по синонимам в плоском словаре. Значения вида {"value": …} (Dolphin) раскрываются."""
    nd = {_norm(k): v for k, v in d.items()}
    for s in _SYN_N[key]:
        v = nd.get(s)
        if isinstance(v, dict) and "value" in v:
            v = v["value"]
        if v not in (None, "", [], {}):
            return v
    return None


_PROXY_OBJECTS = {"proxy", "user_proxy_config", "proxy_config"}


def _flat(d: Any, prefix: str = "", out: Optional[dict] = None) -> dict:
    """Вложенный ответ API -> плоский словарь «последний ключ: значение» (первое вхождение), плюс
    вложенные объекты proxy/user_proxy_config целиком под своими ключами."""
    out = {} if out is None else out
    if isinstance(d, dict):
        for k, v in d.items():
            if _norm(k) in _PROXY_OBJECTS:  # прокси разбирается отдельно: его login/password — не аккаунт
                out.setdefault(_norm(k), v)
                continue
            if isinstance(v, dict) and not ("value" in v and len(v) <= 3):
                out.setdefault(_norm(k), v)
                _flat(v, k, out)
            else:
                out.setdefault(_norm(k), v)
    return out


# ---------------- разбор значений ----------------
def guess_os(platform: Any, ua: Any) -> tuple[str, str]:
    """-> (kind, os)."""
    t = f"{platform or ''} {ua or ''}".lower()
    if "android" in t or "iphone" in t or "ios" in t or "mobile" in t:
        return "mobile", "android"
    if "mac" in t or "darwin" in t:
        return "desktop", "macos"
    if "linux" in t or "x11" in t or "ubuntu" in t:
        return "desktop", "linux"
    return "desktop", "windows"


def parse_proxy(row: dict) -> Optional[Proxy]:
    full = _pick(row, "proxy")
    if isinstance(full, dict):  # вложенный объект прокси (Dolphin: proxy{type,host,port,login,password})
        sub = full
        host = _pick(sub, "proxy_host")
        if host:
            return _proxy_from_parts(sub, nested=True)
        full = None
    if isinstance(full, str) and full.strip() and full.strip().lower() not in ("no_proxy", "noproxy", "none", "direct"):
        try:
            p = Proxy.parse(full.strip())
            ch = _pick(row, "change_url")
            if ch and not p.change_ip_url:
                p.change_ip_url = str(ch)
            return p
        except Exception:  # noqa: BLE001
            pass
    for nested in ("user_proxy_config", "proxy_config"):
        if isinstance(row.get(nested), dict):
            return _proxy_from_parts(row[nested], nested=True)
    return _proxy_from_parts(row) if _pick(row, "proxy_host") and _pick(row, "proxy_port") else None


def _proxy_from_parts(d: dict, nested: bool = False) -> Optional[Proxy]:
    """nested — отдельный объект прокси: там «password»/«username» относятся к прокси, а не к аккаунту."""
    host, port = _pick(d, "proxy_host"), _pick(d, "proxy_port")
    if not host or not port:
        return None
    ptype = str(_pick(d, "proxy_type") or d.get("proxy_soft") or "http").lower()
    ptype = "socks5" if "socks" in ptype else "https" if ptype == "https" else "http"
    try:
        return Proxy(type=ptype, host=str(host).strip(), port=int(str(port).strip()),
                     username=str(_pick(d, "proxy_user") or (d.get("username") if nested else "") or ""),
                     password=str(_pick(d, "proxy_pass") or (d.get("password") if nested else "") or ""),
                     change_ip_url=str(_pick(d, "change_url") or ""))
    except ValueError:
        return None


def parse_cookies(v: Any) -> str:
    if not v:
        return ""
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    s = str(v).strip()
    return s if s.startswith(("[", "{")) or "\t" in s else ""


def parse_tags(v: Any) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x.get("name", x) if isinstance(x, dict) else x).strip() for x in v if str(x).strip()]
    return [t.strip() for t in re.split(r"[,;|]", str(v)) if t.strip()]


def row_to_item(row: dict) -> dict:
    """Строка таблицы или профиль из API -> {"profile": Profile, "proxy", "cookies", "account"}."""
    row = {**_flat(row), **{k: v for k, v in row.items() if not isinstance(v, dict)}} if any(
        isinstance(v, dict) for v in row.values()) else row
    kind, os_name = guess_os(_pick(row, "platform"), _pick(row, "ua"))
    notes = _pick(row, "notes")
    if isinstance(notes, dict):
        notes = notes.get("content") or ""
    folder = _pick(row, "folder")
    if isinstance(folder, dict):
        folder = folder.get("name") or ""
    prof = Profile(name=str(_pick(row, "name") or "Импорт").strip()[:120], kind=kind, os=os_name,
                   notes=str(notes or ""), folder=str(folder or ""), tags=parse_tags(_pick(row, "tags")), status="new")
    acc = None
    login, pwd, secret = _pick(row, "acc_login"), _pick(row, "acc_pass"), _pick(row, "totp")
    # «login/password» в таблицах AdsPower — учётка сайта, а не прокси (для прокси там user/pass)
    service = _pick(row, "service")
    plat = _pick(row, "platform")
    if not service and isinstance(plat, str) and "." in plat:  # у AdsPower «platform» — это сайт: facebook.com
        service = plat
    if login or pwd or secret:
        acc = Account(profile_id=0, service=str(service or ""), login=str(login or ""),
                      password=str(pwd or ""), totp=str(secret or "").strip())
    return {"profile": prof, "proxy": parse_proxy(row), "cookies": parse_cookies(_pick(row, "cookies")), "account": acc}


# ---------------- источники ----------------
def read_file(path: str) -> list[dict]:
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        wb = load_workbook(p, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        head = [str(h or "").strip() for h in rows[0]]
        return [{head[i]: v for i, v in enumerate(r) if i < len(head) and head[i] and v not in (None, "")}
                for r in rows[1:] if any(v not in (None, "") for v in r)]
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    if ext == ".json" or text.lstrip().startswith(("[", "{")):
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("data") or data.get("profiles") or data.get("list") or [data]
            if isinstance(data, dict):
                data = data.get("list") or [data]
        return [d for d in data if isinstance(d, dict)]
    dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t") if text.strip() else csv.excel
    return [dict(r) for r in csv.DictReader(io.StringIO(text), dialect=dialect)]


def fetch_dolphin(token: str, say: Callable[[str], None] = lambda s: None) -> list[dict]:
    """Облачный API Dolphin Anty (токен — в личном кабинете, раздел API)."""
    base = "https://dolphin-anty-api.com"
    head = {"Authorization": f"Bearer {token.strip()}"}
    out, page = [], 1
    with httpx.Client(timeout=30, headers=head) as c:
        while True:
            r = c.get(f"{base}/browser_profiles", params={"limit": 50, "page": page})
            if r.status_code in (401, 403):
                raise ValueError("Dolphin: токен не подошёл (401/403)")
            r.raise_for_status()
            data = r.json()
            items = data.get("data") or []
            for it in items:  # в списке не всё — подробности профиля отдельным запросом
                try:
                    d = c.get(f"{base}/browser_profiles/{it['id']}").json()
                    full = d.get("data", d) if isinstance(d, dict) else it
                    it = {**it, **full} if isinstance(full, dict) else it
                except Exception:  # noqa: BLE001
                    pass
                out.append(it)
            say(f"Dolphin: получено профилей {len(out)}")
            last = data.get("last_page") or data.get("meta", {}).get("last_page") or page
            if not items or page >= int(last):
                break
            page += 1
    return out


def fetch_adspower(base: str = "http://local.adspower.net:50325", api_key: str = "",
                   say: Callable[[str], None] = lambda s: None) -> list[dict]:
    """Локальный API AdsPower (AdsPower должен быть запущен; API включается в его настройках)."""
    head = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    out, page = [], 1
    with httpx.Client(timeout=30, headers=head) as c:
        while True:
            r = c.get(f"{base.rstrip('/')}/api/v1/user/list", params={"page": page, "page_size": 100})
            d = r.json()
            if d.get("code") not in (0, "0", None):
                raise ValueError(f"AdsPower: {d.get('msg') or d}")
            items = (d.get("data") or {}).get("list") or []
            out += items
            say(f"AdsPower: получено профилей {len(out)}")
            if len(items) < 100:
                break
            page += 1
    return out


async def import_items(manager, rows: list[dict], say: Callable[[str], None] = lambda s: None,
                       cookies: bool = True) -> dict:
    """Создаёт профили. Прокси с тем же адресом и логином не дублируются."""
    existing = {(p.type, p.host.lower(), p.port, p.username): p.id for p in db.list_proxies()}
    created, n_cookies, n_acc, errors = [], 0, 0, []
    for i, row in enumerate(rows, 1):
        try:
            it = row_to_item(row)
        except Exception as e:  # noqa: BLE001
            errors.append(f"строка {i}: {e}")
            continue
        p: Profile = it["profile"]
        px = it["proxy"]
        if px:
            key = (px.type, px.host.lower(), px.port, px.username)
            if key not in existing:
                existing[key] = db.save_proxy(px).id
            p.proxy_id = existing[key]
        p = manager.create_profile(p)
        created.append(p.id)
        if it["account"]:
            a = it["account"]
            a.profile_id = p.id
            db.save_account(a)
            n_acc += 1
        if cookies and it["cookies"]:
            try:
                n_cookies += await manager.set_cookies(p.id, it["cookies"])
            except Exception as e:  # noqa: BLE001
                errors.append(f"#{p.id} cookies: {str(e)[:120]}")
        say(f"Импорт: {i}/{len(rows)} «{p.name}» → #{p.id}")
    return {"profiles": created, "cookies": n_cookies, "accounts": n_acc, "errors": errors}
