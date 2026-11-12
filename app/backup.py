"""Экспорт/импорт профилей одним файлом и резервные копии.

Архив .zip (расширение .caprofile — чтобы не путать с обычными zip):
  claudeanty.json        — профили (настройки + отпечаток), их прокси, cookies, по желанию пароли и 2FA;
  profiles/<n>/…         — папка браузера: история, localStorage, IndexedDB, расширения, закладки…
                           (без кэшей — они большие и восстанавливаются сами).

Cookies и сохранённые пароли Chrome зашифрованы ключом Windows этого ПК, поэтому на другом компьютере
файл Cookies бесполезен. Cookies кладутся в архив ещё и списком (JSON) и при импорте добавляются заново.
Если папка пришла с другого ПК, ключ Chrome из «Local State» сбрасывается — Chrome создаст новый.

Пароли и 2FA из claudeAnty в базе зашифрованы DPAPI (тоже привязка к ПК), поэтому в архив они попадают
только по явной галочке.

Шифрование файла целиком (crypto.encrypt_file, AES-256-GCM):
  • паролем — для передачи профиля другому человеку или на другой ПК: без пароля не прочитать ни cookies,
    ни пароли, ни историю;
  • ключом Windows (DPAPI) — резервные копии по умолчанию: открываются только на этом ПК под этой учётной записью.
Без шифрования cookies и (по галочке) пароли лежат в архиве открытым текстом.
"""
import contextlib
import json
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import Callable, Optional

from . import crypto, db, settings
from .config import DATA_DIR, PROFILES_DIR
from .models import Account, Profile, Proxy

FORMAT = 1
MANIFEST = "claudeanty.json"
EXT = ".caprofile"
BACKUP_DIR = DATA_DIR / "backups"

# папки и файлы, которые не нужны в архиве (кэши, служебные загрузки компонентов, блокировки)
_SKIP_DIRS = {
    "Cache", "Code Cache", "GPUCache", "DawnWebGPUCache", "DawnGraphiteCache", "GrShaderCache", "ShaderCache",
    "GraphiteDawnCache", "GPUPersistentCache", "CacheStorage", "ScriptCache", "component_crx_cache",
    "extensions_crx_cache", "Crashpad", "BrowserMetrics", "optimization_guide_model_store", "OptimizationHints",
    "OptimizationGuideModelsManifest", "OnDeviceHeadSuggestModel", "Safe Browsing", "segmentation_platform",
    "WidevineCdm", "screen_ai", "SODA", "SODALanguagePacks", "hyphen-data", "ZxcvbnData", "Subresource Filter",
    "FirstPartySetsPreloaded", "MEIPreload", "CertificateRevocation", "OriginTrials", "PKIMetadata",
    "SSLErrorAssistant", "TrustTokenKeyCommitments", "FileTypePolicies", "Crowd Deny", "AutofillStates",
    "PrivacySandboxAttestationsPreloaded", "ProbabilisticRevealTokenRegistry", "CookieReadinessList",
    "SafetyTips", "RecoveryImproved", "WasmTtsEngine", "ActorSafetyLists", "CaptchaProviders",
    "AmountExtractionHeuristicRegexes", "Variations", "VariationsSeedV2", "VariationsSafeSeedV2",
}
_SKIP_FILES = {"SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile", "BrowserMetrics-spare.pma"}


def _iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if fn in _SKIP_FILES or fn.endswith((".tmp", ".bdic")):
                continue
            yield Path(dirpath) / fn


def _proxy_key(p: Proxy) -> tuple:
    return (p.type, p.host.lower(), p.port, p.username)


# ---------------- экспорт ----------------
async def export_profiles(manager, pids: list[int], path: str, include_secrets: bool = False,
                          progress: Optional[Callable[[str], None]] = None, cookies: bool = True,
                          all_proxies: bool = False, password: str = "", dpapi: bool = False) -> dict:
    """Пишет архив. Работающие профили не останавливаются: cookies берутся из живого браузера,
    файлы, занятые браузером, пропускаются (их число — в результате).
    password — зашифровать файл паролем; dpapi — ключом Windows этого ПК (резервные копии)."""
    say = progress or (lambda s: None)
    manifest = {"format": FORMAT, "app": "claudeAnty", "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "profiles": [], "proxies": []}
    skipped = 0
    tmp = Path(path + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for n, pid in enumerate(pids):
            p = db.get_profile(pid)
            if not p:
                continue
            say(f"Экспорт #{pid} «{p.name}» ({n + 1}/{len(pids)})…")
            entry: dict = {"profile": p.model_dump(), "dir": f"profiles/{n}/", "cookies": None, "accounts": None}
            px = db.get_proxy(p.proxy_id)
            entry["proxy"] = px.model_dump() if px else None
            if cookies:
                try:
                    entry["cookies"] = await manager.get_cookies(pid)
                except Exception as e:  # noqa: BLE001
                    entry["cookies_error"] = str(e)[:200]
            if include_secrets:
                entry["accounts"] = [a.model_dump(exclude={"id", "profile_id"}) for a in db.list_accounts(pid)]
            udd = PROFILES_DIR / str(pid)
            if udd.exists():
                for f in _iter_files(udd):
                    try:
                        z.write(f, entry["dir"] + f.relative_to(udd).as_posix())
                    except (PermissionError, OSError):
                        skipped += 1  # файл занят работающим браузером
            manifest["profiles"].append(entry)
        if all_proxies:
            manifest["proxies"] = [x.model_dump() for x in db.list_proxies()]
        z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1))
    if password or dpapi:
        say("Шифрование файла…")
        enc = Path(path + ".enc.part")
        try:
            crypto.encrypt_file(tmp, enc, password, crypto.MODE_PASSWORD if password else crypto.MODE_DPAPI)
        finally:
            tmp.unlink(missing_ok=True)
        tmp = enc
    os.replace(tmp, path)
    size = Path(path).stat().st_size
    return {"path": path, "profiles": len(manifest["profiles"]), "skipped_files": skipped, "size": size,
            "encrypted": "password" if password else "dpapi" if dpapi else ""}


# ---------------- импорт ----------------
def encryption(path: str) -> str:
    """"" — обычный архив, "password" — нужен пароль, "dpapi" — ключ Windows."""
    mode = crypto.is_encrypted_file(path)
    return {0: "", crypto.MODE_PASSWORD: "password", crypto.MODE_DPAPI: "dpapi"}.get(mode, "unknown")


@contextlib.contextmanager
def opened(path: str, password: str = ""):
    """Путь к обычному zip: сам файл или расшифрованная временная копия (удаляется по выходе).
    Неверный/пустой пароль -> crypto.PasswordError."""
    if not crypto.is_encrypted_file(path):
        yield path
        return
    tmp_dir = DATA_DIR / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    tmp = tmp_dir / f"import-{os.getpid()}-{time.time_ns()}.zip"
    try:
        crypto.decrypt_file(path, tmp, password)
        yield str(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def read_manifest(path: str, password: str = "") -> dict:
    with opened(path, password) as plain:
        return _read_manifest(plain)


def _read_manifest(path: str) -> dict:
    with zipfile.ZipFile(path) as z:
        m = json.loads(z.read(MANIFEST).decode("utf-8"))
    if m.get("app") != "claudeAnty":
        raise ValueError("это не архив claudeAnty")
    if m.get("format", 0) > FORMAT:
        raise ValueError("архив создан более новой версией claudeAnty")
    return m


def _fix_foreign_chrome_dir(udd: Path):
    """Папка с другого ПК: ключ шифрования Chrome не расшифровать -> сбросить его и зашифрованные файлы.
    Chrome создаст новый ключ; cookies добавятся из JSON, история/localStorage/IndexedDB не зашифрованы."""
    ls_path = udd / "Local State"
    try:
        ls = json.loads(ls_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    if crypto.chrome_key_readable(ls):
        return False
    ls.pop("os_crypt", None)
    ls_path.write_text(json.dumps(ls), encoding="utf-8")
    for prof in [udd / "Default", *udd.glob("Profile *")]:
        for name in ("Network/Cookies", "Network/Cookies-journal", "Cookies", "Cookies-journal"):
            try:
                (prof / name).unlink()
            except OSError:
                pass
    return True


async def import_archive(manager, path: str, progress: Optional[Callable[[str], None]] = None,
                         password: str = "") -> dict:
    with opened(path, password) as plain:
        return await _import_archive(manager, plain, progress)


async def _import_archive(manager, path: str, progress: Optional[Callable[[str], None]] = None) -> dict:
    say = progress or (lambda s: None)
    m = _read_manifest(path)
    proxies = {_proxy_key(x): x for x in db.list_proxies()}

    def proxy_id(data: Optional[dict]) -> Optional[int]:
        if not data:
            return None
        px = Proxy(**{**data, "id": None})
        hit = proxies.get(_proxy_key(px))
        if hit:
            return hit.id
        px = db.save_proxy(px)
        proxies[_proxy_key(px)] = px
        return px.id

    for data in m.get("proxies", []):
        proxy_id(data)

    created, foreign, cookies_total, accounts_total = [], 0, 0, 0
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        for n, entry in enumerate(m["profiles"]):
            pdata = dict(entry["profile"])
            pdata["id"] = None
            p = Profile(**pdata)
            p.proxy_id = proxy_id(entry.get("proxy"))
            p = db.save_profile(p)  # новый номер: в этой базе он может быть занят
            say(f"Импорт «{p.name}» → #{p.id} ({n + 1}/{len(m['profiles'])})…")
            udd = PROFILES_DIR / str(p.id)
            if udd.exists():
                shutil.rmtree(udd, ignore_errors=True)
            prefix = entry["dir"]
            for name in names:
                if name.startswith(prefix) and not name.endswith("/"):
                    target = udd / name[len(prefix):]
                    if not target.resolve().is_relative_to(udd.resolve()):
                        continue  # защита от путей вида ../../ в чужом архиве
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(name) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            if _fix_foreign_chrome_dir(udd):
                foreign += 1
            for a in entry.get("accounts") or []:
                db.save_account(Account(**{**a, "profile_id": p.id}))
                accounts_total += 1
            if entry.get("cookies"):
                try:
                    cookies_total += await manager.set_cookies(p.id, json.dumps(entry["cookies"]))
                except Exception as e:  # noqa: BLE001
                    say(f"#{p.id}: cookies не восстановлены: {e}")
            created.append(p.id)
    return {"profiles": created, "foreign": foreign, "cookies": cookies_total, "accounts": accounts_total}


# ---------------- резервные копии ----------------
def backup_encryption() -> tuple[str, bool]:
    """-> (пароль, dpapi) для резервных копий по настройкам."""
    mode = settings.get("backup_encryption")
    if mode == "password":
        pw = settings.get("backup_password")
        if pw:
            return pw, False
        return "", True  # пароль не задан — хотя бы ключом Windows, но не открытым текстом
    return "", mode == "dpapi"


async def backup_all(manager, progress: Optional[Callable[[str], None]] = None) -> dict:
    """Все профили и все прокси в data/backups/backup-ГГГГ-ММ-ДД_ЧЧ-ММ.caprofile; старые копии сверх
    лимита удаляются. Пароли и 2FA — по настройке «backup_secrets»; шифрование — «backup_encryption»."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f"backup-{time.strftime('%Y-%m-%d_%H-%M')}{EXT}"
    pids = [p.id for p in db.list_profiles()]
    pw, dp = backup_encryption()
    res = await export_profiles(manager, pids, str(path), include_secrets=bool(settings.get("backup_secrets")),
                                progress=progress, all_proxies=True, password=pw, dpapi=dp)
    keep = max(1, int(settings.get("backup_keep")))
    old = sorted(BACKUP_DIR.glob(f"backup-*{EXT}"))[:-keep]
    for f in old:
        try:
            f.unlink()
        except OSError:
            pass
    res["removed"] = len(old)
    return res


def list_backups() -> list[Path]:
    return sorted(BACKUP_DIR.glob(f"*{EXT}"), reverse=True) if BACKUP_DIR.exists() else []
