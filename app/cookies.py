"""Импорт/экспорт cookies: JSON (формат EditThisCookie / Playwright) и Netscape cookies.txt."""
import json

_SAMESITE_IN = {"no_restriction": "None", "unspecified": "Lax", "lax": "Lax", "strict": "Strict", "none": "None"}


def parse_cookies(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("[") or text.startswith("{"):
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("cookies", [data])
        return [_norm_json(c) for c in data]
    return _parse_netscape(text)


def _norm_json(c: dict) -> dict:
    out = {
        "name": c["name"], "value": c["value"],
        "domain": c.get("domain") or c.get("host", ""), "path": c.get("path", "/"),
        "httpOnly": bool(c.get("httpOnly", False)), "secure": bool(c.get("secure", False)),
    }
    exp = c.get("expirationDate", c.get("expires", -1))
    if exp not in (None, -1) and not c.get("session", False):
        out["expires"] = float(exp)
    ss = str(c.get("sameSite", "Lax")).lower()
    out["sameSite"] = _SAMESITE_IN.get(ss, "Lax")
    if out["sameSite"] == "None":
        out["secure"] = True
    return out


def _parse_netscape(text: str) -> list[dict]:
    cookies = []
    for line in text.splitlines():
        http_only = line.startswith("#HttpOnly_")
        if http_only:
            line = line[len("#HttpOnly_"):]
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        domain, _, path, secure, expires, name, value = parts
        c = {"name": name, "value": value, "domain": domain, "path": path,
             "secure": secure.upper() == "TRUE", "httpOnly": http_only, "sameSite": "Lax"}
        if expires.isdigit() and int(expires) > 0:
            c["expires"] = float(expires)
        cookies.append(c)
    return cookies


def export_json(cookies: list[dict]) -> str:
    out = []
    for c in cookies:
        out.append({
            "name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"],
            "secure": c["secure"], "httpOnly": c["httpOnly"],
            "sameSite": {"None": "no_restriction", "Lax": "lax", "Strict": "strict"}.get(c.get("sameSite", "Lax"), "lax"),
            "session": c.get("expires", -1) == -1,
            **({"expirationDate": c["expires"]} if c.get("expires", -1) != -1 else {}),
        })
    return json.dumps(out, ensure_ascii=False, indent=2)


def export_netscape(cookies: list[dict]) -> str:
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        dom = c["domain"]
        sub = "TRUE" if dom.startswith(".") else "FALSE"
        exp = int(c["expires"]) if c.get("expires", -1) not in (-1, None) else 0
        prefix = "#HttpOnly_" if c["httpOnly"] else ""
        lines.append("\t".join([prefix + dom, sub, c["path"], "TRUE" if c["secure"] else "FALSE", str(exp), c["name"], c["value"]]))
    return "\n".join(lines) + "\n"
