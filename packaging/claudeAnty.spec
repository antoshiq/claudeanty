# PyInstaller: сборка claudeAnty.exe (папка build/dist/claudeAnty). Запуск: packaging/build.ps1
# -*- mode: python ; coding: utf-8 -*-
import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
datas = [
    (str(ROOT / "app" / "fingerprints" / "data"), "app/fingerprints/data"),
    (str(ROOT / "app" / "fingerprints" / "inject"), "app/fingerprints/inject"),
]
binaries = []
hidden = collect_submodules("app") + collect_submodules("uvicorn") + ["cryptography.hazmat.primitives.kdf.scrypt"]

# драйверы браузеров (node + js) и данные Camoufox — не Python-модули, их нужно забрать целиком
for pkg in ("playwright", "patchright", "camoufox", "browserforge", "apify_fingerprint_datapoints", "language_tags"):
    if importlib.util.find_spec(pkg):
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hidden += h

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
              "PySide6.QtMultimedia", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtPdf", "matplotlib", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = ROOT / "packaging" / "claudeAnty.ico"
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="claudeAnty",
    console=False,  # окно без консоли; вывод — в data/logs/app.log
    icon=str(icon) if icon.exists() else None,
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="claudeAnty")
