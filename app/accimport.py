"""Импорт купленных аккаунтов строками: по профилю на строку.

Строка — поля через разделитель в порядке, заданном шаблоном, например «login:pass:2fa:proxy» или
«login|pass|email|email_pass|2fa|proxy». Поля шаблона:
  login, pass (password), 2fa (totp), proxy, email, email_pass, name, notes, - (пропустить поле).

Прокси сам содержит двоеточия (host:port:user:pass, socks5://u:p@h:port) — при разделителе «:» поле proxy
забирает все «лишние» части строки. Если 2FA стоит прямо перед прокси и в строке его нет
(«login:pass:host:port»), поле не похожее на секрет base32 считается началом прокси.
Пароль с двоеточием — используйте шаблон с разделителем «|», «;» или табуляцией.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from . import db, totp
from .models import Account, Profile, Proxy

TOKENS = {
    "login": "login", "user": "login", "username": "login",
    "pass": "password", "password": "password", "pwd": "password",
    "2fa": "totp", "totp": "totp", "secret": "totp", "fa": "totp",
    "proxy": "proxy",
    "email": "email", "mail": "email",
    "email_pass": "email_pass", "mail_pass": "email_pass", "emailpass": "email_pass",
    "name": "name", "notes": "notes", "note": "notes",
    "-": "skip", "_": "skip", "skip": "skip", "*": "skip",
}
SEPARATORS = ("|", ";", "\t", ":")
DEFAULT_TEMPLATE = "login:pass:2fa:proxy"


@dataclass
class Row:
    line_no: int
    login: str = ""
    password: str = ""
    totp: str = ""
    proxy: Optional[Proxy] = None
    email: str = ""
    email_pass: str = ""
    name: str = ""
    notes: str = ""
    warnings: list[str] = field(default_factory=list)


def parse_template(template: str) -> tuple[str, list[str]]:
    """«login:pass:2fa:proxy» -> (":", ["login", "password", "totp", "proxy"])."""
    t = template.strip()
    if not t:
        raise ValueError("пустой шаблон")
    sep = next((s for s in SEPARATORS if s in t), None)
    parts = [x.strip().lower() for x in (t.split(sep) if sep else [t])]
    out = []
    for x in parts:
        if x not in TOKENS:
            raise ValueError(f"неизвестное поле «{x}» в шаблоне — можно: login, pass, 2fa, proxy, email, "
                             "email_pass, name, notes, - (пропустить)")
        out.append(TOKENS[x])
    named = [x for x in out if x != "skip"]
    if len(named) != len(set(named)):
        raise ValueError("поле указано в шаблоне дважды")
    if "login" not in out and "email" not in out:
        raise ValueError("в шаблоне нужен login или email")
    return sep or ":", out


def looks_secret(s: str) -> bool:
    """Похоже ли поле на секрет 2FA (base32 от 16 символов или otpauth-ссылка), а не на хост прокси."""
    s = s.strip()
    if not s:
        return False
    if s.lower().startswith("otpauth"):
        return True
    clean = re.sub(r"[\s-]", "", s).upper().rstrip("=")
    return len(clean) >= 16 and re.fullmatch(r"[A-Z2-7]+", clean) is not None and totp.valid(s)


def looks_host(s: str) -> bool:
    """Начало прокси: схема (socks5, http…) или хост / IP."""
    s = s.strip().lower()
    return s in ("http", "https", "socks5", "socks", "socks5h") or (
        re.fullmatch(r"[a-z0-9.\-]+", s) is not None and ("." in s or s == "localhost"))


def split_line(line: str, sep: str, fields: list[str]) -> dict[str, str]:
    parts = line.split(sep)
    if sep == ":" and "proxy" in fields:
        i = fields.index("proxy")
        after = len(fields) - i - 1
        # 2FA прямо перед прокси, но в строке его нет: «login:pass:host:port[:user:pass]»
        if i > 0 and fields[i - 1] == "totp" and len(parts) > i - 1 and not looks_secret(parts[i - 1]) \
                and looks_host(parts[i - 1]) and len(parts) - after > i:
            parts = parts[:i - 1] + [""] + parts[i - 1:]
        end = max(i, len(parts) - after)
        proxy = sep.join(parts[i:end])
        parts = parts[:i] + [proxy] + parts[end:]
        # «socks5://u:p@h:1080» после разбиения по «:» склеен обратно целиком
    elif sep == ":" and "totp" in fields and len(parts) > len(fields):
        # otpauth://totp/...: ссылка 2FA с двоеточием
        i = fields.index("totp")
        extra = len(parts) - len(fields)
        parts = parts[:i] + [sep.join(parts[i:i + extra + 1])] + parts[i + extra + 1:]
    out = {}
    for k, v in zip(fields, parts):
        if k != "skip":
            out[k] = v.strip()
    return out


def parse_lines(text: str, template: str = DEFAULT_TEMPLATE) -> tuple[list[Row], list[tuple[int, str]]]:
    """-> (строки, ошибки [(номер строки, текст)]). Пустые строки и строки с # пропускаются."""
    sep, fields = parse_template(template)
    rows, errors = [], []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        d = split_line(line, sep, fields)
        r = Row(line_no=n, login=d.get("login", ""), password=d.get("password", ""), email=d.get("email", ""),
                email_pass=d.get("email_pass", ""), name=d.get("name", ""), notes=d.get("notes", ""))
        if not (r.login or r.email):
            errors.append((n, "нет логина"))
            continue
        sec = d.get("totp", "")
        if sec:
            if totp.valid(sec):
                r.totp = sec
            else:
                errors.append((n, f"2FA «{sec[:24]}» — не секрет base32 / otpauth"))
                continue
        if d.get("proxy"):
            try:
                r.proxy = Proxy.parse(d["proxy"])
                if not re.fullmatch(r"[A-Za-z0-9.\-\[\]:]+", r.proxy.host):
                    raise ValueError(f"не похоже на прокси: «{d['proxy'][:60]}»")
            except ValueError as e:
                errors.append((n, str(e)))
                continue
        if len(line.split(sep)) < len([f for f in fields if f not in ("proxy", "totp")]):
            r.warnings.append("полей меньше, чем в шаблоне")
        rows.append(r)
    return rows, errors


def _proxy_key(p: Proxy) -> tuple:
    return (p.type, p.host.lower(), p.port, p.username, p.password)


def create_profiles(manager, rows: list[Row], *, service: str = "", url: str = "", template: Optional[Profile] = None,
                    name_mode: str = "login", base: str = "Profile", folder: str = "", tags: Optional[list[str]] = None,
                    free_proxies: Optional[list[Proxy]] = None, defaults: Optional[dict] = None) -> dict:
    """Профиль на строку: аккаунт (логин/пароль/2FA) в «Пароли и 2FA», почта отдельным аккаунтом «Почта»,
    прокси — свой из строки (одинаковые не дублируются), иначе следующий из free_proxies.
    name_mode: login — профиль называется логином, number — «base N»."""
    existing = {_proxy_key(p): p for p in db.list_proxies()}
    free = list(free_proxies or [])
    defaults = defaults or {}
    tpl = template or Profile(name=base, kind=defaults.get("kind", "desktop"), engine=defaults.get("engine", "chromium"),
                              os=defaults.get("os", "windows"), device=defaults.get("device", ""),
                              auto_geo=defaults.get("auto_geo", True), status="new")
    num = db.next_number(base)
    created, new_proxies = [], []
    for r in rows:
        p = tpl.model_copy(deep=True)
        p.id = None
        p.fingerprint, p.last_check, p.last_external, p.fp_history = {}, {}, {}, []
        p.ban_reason, p.captcha_streak, p.warm_plan = "", {}, {}
        p.status = "new"
        if name_mode == "login":
            p.name = r.name or r.login or r.email
        else:
            p.name = r.name or f"{base} {num}"
            num += 0 if r.name else 1
        if folder:
            p.folder = folder
        if tags:
            p.tags = list(dict.fromkeys([*p.tags, *tags]))
        p.proxy_id = None
        if r.proxy:
            hit = existing.get(_proxy_key(r.proxy))
            if not hit:
                hit = db.save_proxy(r.proxy)
                existing[_proxy_key(hit)] = hit
                new_proxies.append(hit)
            p.proxy_id = hit.id
        elif free:
            p.proxy_id = free.pop(0).id
        if r.notes:
            p.notes = (p.notes + "\n" + r.notes).strip()
        p = manager.create_profile(p)
        if r.login or r.password or r.totp:
            db.save_account(Account(profile_id=p.id, service=service, url=url, login=r.login or r.email,
                                    password=r.password, totp=r.totp))
        if r.email and (r.email_pass or r.email != r.login):
            db.save_account(Account(profile_id=p.id, service="Почта", login=r.email, password=r.email_pass))
        try:
            from .activity import log as alog
            alog(p.id, "edit", f"создан импортом аккаунтов (строка {r.line_no})")
        except Exception:  # noqa: BLE001
            pass
        created.append(p.id)
    return {"profiles": created, "new_proxies": [p.id for p in new_proxies],
            "without_proxy": sum(1 for pid in created if not (db.get_profile(pid) or Profile(name="")).proxy_id)}
