"""Шифрование секретов без мастер-пароля: Windows DPAPI (CryptProtectData).

Ключ привязан к учётной записи Windows на этом ПК: другой пользователь или другой компьютер расшифровать
данные не сможет, а сам владелец — без ввода пароля. Так же Chrome шифрует свои cookies и пароли.
На других ОС (нет DPAPI) данные хранятся с пометкой «plain» — приложение рассчитано на Windows.
"""
import base64
import ctypes
import sys
from ctypes import wintypes

_ENTROPY = b"claudeAnty secrets v1"  # дополнительная «соль»: другие программы того же пользователя не прочтут


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    b = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    b._buf = buf  # держим ссылку, пока blob жив
    return b


def _call(fn_name: str, data: bytes, entropy: bytes | None) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    src, out = _blob(data), _Blob()
    ent = _blob(entropy) if entropy is not None else None
    fn = getattr(crypt32, fn_name)
    ok = fn(ctypes.byref(src), None, ctypes.byref(ent) if ent else None, None, None, 0x01, ctypes.byref(out))  # UI_FORBIDDEN
    if not ok:
        raise OSError(f"{fn_name}: ошибка {ctypes.GetLastError()}")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def dpapi_encrypt(data: bytes, entropy: bytes | None = _ENTROPY) -> bytes:
    return _call("CryptProtectData", data, entropy)


def dpapi_decrypt(data: bytes, entropy: bytes | None = _ENTROPY) -> bytes:
    return _call("CryptUnprotectData", data, entropy)


def encrypt(text: str) -> str:
    """Строка -> «dpapi:<base64>» (или «plain:…» не на Windows)."""
    if sys.platform != "win32":
        return "plain:" + base64.b64encode(text.encode()).decode()
    return "dpapi:" + base64.b64encode(dpapi_encrypt(text.encode("utf-8"))).decode()


def decrypt(token: str) -> str:
    kind, _, body = token.partition(":")
    raw = base64.b64decode(body)
    if kind == "plain":
        return raw.decode()
    if kind == "dpapi":
        return dpapi_decrypt(raw).decode("utf-8")
    raise ValueError("неизвестный формат шифрования")


def chrome_key_readable(local_state: dict) -> bool:
    """Может ли этот ПК/пользователь расшифровать ключ cookies и паролей Chrome из «Local State».
    Нет — значит, папка профиля пришла с другого компьютера: зашифрованные cookies в ней бесполезны."""
    if sys.platform != "win32":
        return True
    key = (local_state.get("os_crypt") or {}).get("encrypted_key")
    if not key:
        return True
    try:
        raw = base64.b64decode(key)
        if raw.startswith(b"DPAPI"):
            raw = raw[5:]
        dpapi_decrypt(raw, None)
        return True
    except Exception:  # noqa: BLE001
        return False
