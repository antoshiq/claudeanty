"""ОС этого компьютера и предупреждение, если профиль притворяется другой ОС.

Профиль-компьютер лучше всего держится на той же ОС, что и ПК: браузер рисует canvas и текст шрифтами
и движком рендера этого компьютера, поэтому macOS/Linux-профиль на Windows выдаёт себя — шрифты на
canvas виндовые, сглаживание другое, fingerprint.com помечает такой профиль «Browser Tampering».
"""
import sys

TITLES = {"windows": "Windows", "macos": "macOS", "linux": "Linux", "android": "Android"}


def host_os() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def mismatch(kind: str, os_name: str) -> bool:
    """Профиль-компьютер с ОС, отличной от ОС этого ПК (телефоны — отдельная история, см. подсказку в форме)."""
    return kind != "mobile" and bool(os_name) and os_name != host_os()


def warning(kind: str, os_name: str, short: bool = False) -> str:
    if not mismatch(kind, os_name):
        return ""
    want, have = TITLES.get(os_name, os_name), TITLES[host_os()]
    if short:
        return f"ОС профиля {want} ≠ ОС компьютера {have}: шрифты и рендер canvas выдают подмену"
    return (f"Профиль {want} на компьютере с {have} заметнее: браузер рисует текст и canvas шрифтами и рендером "
            f"{have}, а fingerprint.com помечает такой профиль «Browser Tampering». Надёжнее всего — {have}, "
            "как у этого ПК.")
