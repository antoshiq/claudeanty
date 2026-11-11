"""Шрифты ОС профиля, которых нет на этом ПК (Helvetica Neue у macOS-профиля, Ubuntu у Linux-профиля…).

Сайт определяет шрифты по ширине текста и через FontFace(name, local(name)).load(). Если шрифт ОС профиля
отсутствует, профиль «macOS» без Helvetica Neue выглядит подозрительно, а набор шрифтов выдаёт Windows.
Поэтому каждому отсутствующему шрифту ОС профиля назначается «псевдоним» — установленный шрифт похожего
типа (без засечек → Arial, с засечками → Times New Roman, моноширинный → Courier New, эмодзи → Segoe UI Emoji).
stealth.js подставляет псевдоним в замеры, рисование на canvas и в FontFace.load. Метрики у псевдонима
свои (Arial метрически совместим с Helvetica, Times New Roman — с Times, Courier New — с Courier),
поэтому для самых частых шрифтов размеры совпадают с настоящими.
"""
import re
import sys
from functools import lru_cache

from .generator import FONTS

_STYLE = re.compile(r"\s+(Bold|Italic|Oblique|Light|Semilight|Semibold|SemiBold|Black|Heavy|Medium|Thin|Regular|"
                    r"Condensed|Narrow|ExtraLight|Extra|Ultra|Demi|Book|Variable|&.*)\b.*$", re.I)


@lru_cache(maxsize=1)
def host_fonts() -> frozenset:
    """Имена установленных шрифтов (нижний регистр): из реестра Windows, системного и пользовательского."""
    names: set[str] = set()
    if sys.platform != "win32":
        return frozenset()
    import winreg
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts") as k:
                i = 0
                while True:
                    try:
                        name = winreg.EnumValue(k, i)[0]
                    except OSError:
                        break
                    i += 1
                    name = re.sub(r"\s*\((TrueType|OpenType|All res)\)\s*$", "", name, flags=re.I)
                    for part in name.split("&"):  # «Cambria & Cambria Math»
                        part = part.strip()
                        if part:
                            names.add(part.lower())
                            names.add(_STYLE.sub("", part).lower())
        except OSError:
            pass
    return frozenset(names)


def installed(family: str) -> bool:
    if sys.platform != "win32":
        return True  # на других ОС не проверяем — псевдонимов не будет
    return family.lower() in host_fonts()


def _target(family: str) -> str:
    f = family.lower()
    if "emoji" in f:
        return "Segoe UI Emoji"
    if re.search(r"mono|courier|consol|menlo|monaco|code|typewriter|fixed", f):
        return "Courier New"
    if re.search(r"serif(?!.*sans)|times|georgia|baskerville|garamond|palatino|bodoni|didot|charter|hoefler|cochin|"
                 r"caslon|cambria|book antiqua|century|bookman|rockwell|perpetua", f) and "sans" not in f:
        return "Times New Roman"
    return "Arial"


def aliases(os_name: str) -> dict[str, str]:
    """{шрифт ОС профиля, которого нет на ПК: установленная замена}."""
    out = {}
    for fam in FONTS.get(os_name, []):
        if not installed(fam):
            t = _target(fam)
            if installed(t):
                out[fam] = t
    return out


@lru_cache(maxsize=1)
def known() -> list[str]:
    """Все «системные» шрифты из наших списков ОС — по ним FontFace(local) скрытого шрифта отклоняется."""
    return sorted({f for fonts in FONTS.values() for f in fonts} | set(host_fonts_display()))


def host_fonts_display() -> list[str]:
    return [n for n in host_fonts()]
