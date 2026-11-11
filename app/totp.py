"""Коды 2FA (TOTP, RFC 6238) — как в Google Authenticator.

Секрет принимается в любом привычном виде: base32 с пробелами и в любом регистре («jbsw y3dp ehpk 3pxp»)
или ссылкой otpauth://totp/…?secret=…&digits=…&period=…&algorithm=… (её содержит QR-код сайта).
"""
import base64
import hashlib
import hmac
import struct
import time
from urllib.parse import parse_qs, unquote, urlparse

_ALGOS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


def parse(secret: str) -> dict:
    """-> {"key": bytes, "digits": 6, "period": 30, "algo": "SHA1", "label": ""}"""
    s = secret.strip()
    params = {"digits": 6, "period": 30, "algo": "SHA1", "label": ""}
    if s.lower().startswith("otpauth://"):
        u = urlparse(s)
        if u.netloc.lower() != "totp":
            raise ValueError("поддерживаются только коды по времени (otpauth://totp)")
        q = {k.lower(): v[0] for k, v in parse_qs(u.query).items()}
        s = q.get("secret", "")
        params["digits"] = int(q.get("digits", 6))
        params["period"] = int(q.get("period", 30))
        params["algo"] = q.get("algorithm", "SHA1").upper()
        params["label"] = unquote(u.path.lstrip("/"))
    clean = "".join(s.split()).replace("-", "").upper().rstrip("=")
    if not clean:
        raise ValueError("пустой секрет")
    try:
        key = base64.b32decode(clean + "=" * (-len(clean) % 8))
    except Exception as e:  # noqa: BLE001
        raise ValueError("секрет должен быть в base32 (буквы A–Z и цифры 2–7)") from e
    if params["algo"] not in _ALGOS:
        raise ValueError(f"неизвестный алгоритм {params['algo']}")
    params["key"] = key
    return params


def code(secret: str, at: float | None = None) -> str:
    p = parse(secret)
    counter = int((time.time() if at is None else at) // p["period"])
    mac = hmac.new(p["key"], struct.pack(">Q", counter), _ALGOS[p["algo"]]).digest()
    off = mac[-1] & 0x0F
    num = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** p["digits"])
    return str(num).zfill(p["digits"])


def remaining(secret: str, at: float | None = None) -> int:
    """Сколько секунд текущий код ещё действителен."""
    period = parse(secret)["period"]
    now = time.time() if at is None else at
    return period - int(now) % period


def valid(secret: str) -> bool:
    try:
        parse(secret)
        return True
    except ValueError:
        return False
