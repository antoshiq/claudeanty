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


# ---------------- зашифрованные файлы (передача профилей, резервные копии) ----------------
# Формат: MAGIC | режим (1 байт) | длина заголовка (2 байта) | заголовок | префикс nonce (8 байт) | блоки.
#   режим 1 — пароль: заголовок = соль 16 байт, ключ = scrypt(пароль, соль, n=2^15, r=8, p=1);
#   режим 2 — ключ Windows: заголовок = случайный ключ AES-256, зашифрованный DPAPI (только этот ПК и пользователь).
# Блок: длина (4 байта) | AES-256-GCM(до 1 МБ) с nonce = префикс + номер блока, AAD = «last»/«more» —
# подмена, перестановка и обрезка файла обнаруживаются. Файл не грузится в память целиком.
FILE_MAGIC = b"CAENC1\n"
MODE_PASSWORD, MODE_DPAPI = 1, 2
_CHUNK = 1 << 20


class PasswordError(ValueError):
    """Файл зашифрован паролем, а пароль не задан или неверный."""


def _kdf(password: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(password.encode("utf-8"))


def is_encrypted_file(path) -> int:
    """0 — обычный файл, иначе режим шифрования (MODE_PASSWORD / MODE_DPAPI)."""
    try:
        with open(path, "rb") as f:
            head = f.read(len(FILE_MAGIC) + 1)
    except OSError:
        return 0
    return head[-1] if head.startswith(FILE_MAGIC) and len(head) > len(FILE_MAGIC) else 0


def encrypt_file(src, dst, password: str = "", mode: int = MODE_PASSWORD) -> None:
    import os
    import struct
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if mode == MODE_PASSWORD:
        if not password:
            raise PasswordError("нужен пароль")
        header = os.urandom(16)
        key = _kdf(password, header)
    elif mode == MODE_DPAPI:
        key = os.urandom(32)
        header = dpapi_encrypt(key) if sys.platform == "win32" else b"plain" + key
    else:
        raise ValueError("неизвестный режим шифрования")
    aes, prefix = AESGCM(key), os.urandom(8)
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        fo.write(FILE_MAGIC + bytes([mode]) + struct.pack(">H", len(header)) + header + prefix)
        n, cur = 0, fi.read(_CHUNK)
        while True:
            nxt = fi.read(_CHUNK)
            last = not nxt
            ct = aes.encrypt(prefix + struct.pack(">I", n), cur, b"last" if last else b"more")
            fo.write(struct.pack(">I", len(ct)) + ct)
            if last:
                break
            n, cur = n + 1, nxt


def decrypt_file(src, dst, password: str = "") -> None:
    import struct
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    with open(src, "rb") as fi:
        if fi.read(len(FILE_MAGIC)) != FILE_MAGIC:
            raise ValueError("файл не зашифрован claudeAnty")
        mode = fi.read(1)[0]
        (hlen,) = struct.unpack(">H", fi.read(2))
        header = fi.read(hlen)
        if mode == MODE_PASSWORD:
            if not password:
                raise PasswordError("файл защищён паролем")
            key = _kdf(password, header)
        elif mode == MODE_DPAPI:
            if header.startswith(b"plain"):
                key = header[5:]
            else:
                try:
                    key = dpapi_decrypt(header)
                except OSError as e:
                    raise ValueError("файл зашифрован ключом Windows другого компьютера или пользователя — "
                                     "открыть его можно только там, где он создан") from e
        else:
            raise ValueError("неизвестный режим шифрования (файл от более новой версии?)")
        aes, prefix = AESGCM(key), fi.read(8)
        with open(dst, "wb") as fo:
            n = 0
            while True:
                raw = fi.read(4)
                if len(raw) < 4:
                    raise ValueError("файл обрезан (не скачался или не скопировался до конца)")
                (ln,) = struct.unpack(">I", raw)
                ct = fi.read(ln)
                nonce = prefix + struct.pack(">I", n)
                try:
                    pt = aes.decrypt(nonce, ct, b"more")
                    fo.write(pt)
                    n += 1
                    continue
                except InvalidTag:
                    pass
                try:
                    pt = aes.decrypt(nonce, ct, b"last")
                except InvalidTag:
                    if n == 0 and mode == MODE_PASSWORD:
                        raise PasswordError("неверный пароль") from None
                    raise ValueError("файл повреждён или изменён") from None
                fo.write(pt)
                if fi.read(1):
                    raise ValueError("файл повреждён: данные после конца")
                return
