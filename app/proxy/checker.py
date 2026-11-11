import httpx

from ..models import Proxy


async def check_proxy(p: Proxy, timeout: float = 15.0, direct: bool = False) -> dict:
    """Проверяет прокси и заполняет ip/страну/таймзону/координаты (direct=True — без прокси, ваш IP)."""
    try:
        async with httpx.AsyncClient(proxy=None if direct else p.url(), timeout=timeout) as c:
            r = await c.get("http://ip-api.com/json/?fields=status,message,query,countryCode,timezone,lat,lon")
            d = r.json()
        if d.get("status") != "success":
            p.status, p.error = "fail", d.get("message", "unknown")
            return {"ok": False, "error": p.error}
        p.ip = d["query"]
        p.country = d["countryCode"]
        p.timezone = d["timezone"]
        p.lat, p.lon = d["lat"], d["lon"]
        p.status, p.error = "ok", ""
        return {"ok": True, "ip": p.ip, "country": p.country, "timezone": p.timezone, "lat": p.lat, "lon": p.lon}
    except Exception as e:  # noqa: BLE001
        p.status, p.error = "fail", str(e) or type(e).__name__
        return {"ok": False, "error": p.error}


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
