import re

import httpx

from ..models import Proxy

IP_TYPES = {"residential": "Резидентный", "mobile": "Мобильный", "hosting": "Хостинг/VPN", "proxy": "Известный прокси"}
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


async def tcp_fingerprint(client: httpx.AsyncClient) -> tuple[str, int]:
    """ОС и MTU выходного узла по пассивному TCP/IP-отпечатку (p0f на стороне browserleaks.com).
    Сайты с таким анализом видят ОС прокси-сервера, а не браузера: Windows-профиль через прокси
    «на Linux» — заметная несостыковка."""
    html = (await client.get("https://browserleaks.com/ip", headers={"User-Agent": _UA})).text
    i = html.find("TCP/IP Fingerprint")
    if i < 0:
        return "", 0
    block = html[i:i + 1500]
    os_m = re.search(r"<td>OS</td>\s*<td>([^<]*)</td>", block)
    mtu_m = re.search(r"<td>MTU</td>\s*<td>(\d+)", block)
    return (os_m.group(1).strip() if os_m else "", int(mtu_m.group(1)) if mtu_m else 0)


async def check_proxy(p: Proxy, timeout: float = 15.0, direct: bool = False, deep: bool = True) -> dict:
    """Проверяет прокси и заполняет ip/страну/таймзону/координаты, тип IP и ОС по TCP/IP
    (direct=True — без прокси, ваш IP; deep=False — без TCP/IP-отпечатка, быстрее)."""
    try:
        async with httpx.AsyncClient(proxy=None if direct else p.url(), timeout=timeout) as c:
            r = await c.get("http://ip-api.com/json/?fields=status,message,query,countryCode,timezone,lat,lon,"
                            "mobile,proxy,hosting")
            d = r.json()
            if d.get("status") != "success":
                p.status, p.error = "fail", d.get("message", "unknown")
                return {"ok": False, "error": p.error}
            p.ip = d["query"]
            p.country = d["countryCode"]
            p.timezone = d["timezone"]
            p.lat, p.lon = d["lat"], d["lon"]
            p.ip_type = "mobile" if d.get("mobile") else "hosting" if d.get("hosting") else \
                "proxy" if d.get("proxy") else "residential"
            p.status, p.error = "ok", ""
            if deep:
                try:
                    p.tcp_os, p.mtu = await tcp_fingerprint(c)
                except Exception:  # noqa: BLE001 — не критично: прокси рабочий
                    pass
        return {"ok": True, "ip": p.ip, "country": p.country, "timezone": p.timezone, "lat": p.lat, "lon": p.lon,
                "ip_type": p.ip_type, "tcp_os": p.tcp_os, "mtu": p.mtu}
    except Exception as e:  # noqa: BLE001
        p.status, p.error = "fail", str(e) or type(e).__name__
        return {"ok": False, "error": p.error}


_OS_WORDS = {"windows": ("windows",), "macos": ("mac", "ios"), "linux": ("linux",), "android": ("android", "linux")}


def os_matches(profile_os: str, tcp_os: str) -> bool | None:
    """Совпадает ли ОС профиля с ОС прокси по TCP/IP. None — неизвестно."""
    if not tcp_os or tcp_os.lower() in ("unknown", "n/a", "???"):
        return None
    return any(w in tcp_os.lower() for w in _OS_WORDS.get(profile_os, ()))


def os_warning(profile_os: str, proxy: Proxy | None) -> str:
    """Текст предупреждения, если прокси выдаёт другую ОС (или пусто)."""
    if not proxy:
        return ""
    m = os_matches(profile_os, proxy.tcp_os)
    if m is False:
        return (f"Прокси по TCP/IP выглядит как {proxy.tcp_os}, а профиль — {OS_TITLES.get(profile_os, profile_os)}. "
                "Сайты с анализом сетевого стека заметят несовпадение: возьмите прокси на той же ОС "
                "(для телефона — мобильный, для Windows — резидентный на Windows).")
    if proxy.mtu and proxy.mtu < 1500 and proxy.ip_type != "mobile":
        return f"MTU {proxy.mtu} (не 1500) — признак туннеля VPN/прокси."
    return ""


OS_TITLES = {"windows": "Windows", "macos": "macOS", "linux": "Linux", "android": "Android"}


# страна -> локаль по умолчанию
COUNTRY_LOCALE = {
    "US": "en-US", "GB": "en-GB", "CA": "en-CA", "AU": "en-AU", "RU": "ru-RU", "UA": "uk-UA",
    "DE": "de-DE", "FR": "fr-FR", "ES": "es-ES", "IT": "it-IT", "PL": "pl-PL", "NL": "nl-NL",
    "BR": "pt-BR", "PT": "pt-PT", "TR": "tr-TR", "IN": "en-IN", "JP": "ja-JP", "KR": "ko-KR",
    "CN": "zh-CN", "MX": "es-MX", "AR": "es-AR", "KZ": "ru-KZ", "BY": "ru-BY",
    "CH": "de-CH", "AT": "de-AT", "BE": "nl-BE", "SE": "sv-SE", "NO": "nb-NO", "DK": "da-DK", "FI": "fi-FI",
    "CZ": "cs-CZ", "SK": "sk-SK", "RO": "ro-RO", "HU": "hu-HU", "GR": "el-GR", "BG": "bg-BG", "RS": "sr-RS",
    "HR": "hr-HR", "LT": "lt-LT", "LV": "lv-LV", "EE": "et-EE", "IE": "en-IE", "NZ": "en-NZ", "ZA": "en-ZA",
    "IL": "he-IL", "AE": "ar-AE", "SA": "ar-SA", "EG": "ar-EG", "TH": "th-TH", "VN": "vi-VN", "ID": "id-ID",
    "MY": "ms-MY", "SG": "en-SG", "PH": "en-PH", "TW": "zh-TW", "HK": "zh-HK", "CL": "es-CL", "CO": "es-CO",
    "PE": "es-PE", "GE": "ka-GE", "AM": "hy-AM", "AZ": "az-AZ", "UZ": "uz-UZ", "MD": "ro-MD", "NG": "en-NG",
}


def locale_for(country: str) -> str:
    return COUNTRY_LOCALE.get(country, "en-US")
