"""Локальный HTTP-прокси без авторизации, пробрасывающий трафик через upstream-прокси с логином/паролем
(SOCKS5 или HTTP/HTTPS). Chromium не умеет SOCKS5 с авторизацией, а HTTP-авторизацию иначе пришлось бы
обрабатывать перехватом запросов — relay проще и незаметнее для страниц."""
import asyncio
import base64
import struct

from ..models import Proxy


async def _socks5_connect(p: Proxy, host: str, port: int):
    r, w = await asyncio.open_connection(p.host, p.port)
    methods = b"\x00\x02" if p.username else b"\x00"
    w.write(b"\x05" + bytes([len(methods)]) + methods)
    await w.drain()
    ver, method = await r.readexactly(2)
    if method == 2:
        u, pw = p.username.encode(), p.password.encode()
        w.write(b"\x01" + bytes([len(u)]) + u + bytes([len(pw)]) + pw)
        await w.drain()
        _, status = await r.readexactly(2)
        if status != 0:
            raise ConnectionError("SOCKS5 auth failed")
    elif method != 0:
        raise ConnectionError("SOCKS5: no acceptable auth method")
    hb = host.encode()
    w.write(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + struct.pack(">H", port))
    await w.drain()
    head = await r.readexactly(4)
    if head[1] != 0:
        raise ConnectionError(f"SOCKS5 connect failed: {head[1]}")
    atyp = head[3]
    if atyp == 1:
        await r.readexactly(4 + 2)
    elif atyp == 4:
        await r.readexactly(16 + 2)
    else:
        n = (await r.readexactly(1))[0]
        await r.readexactly(n + 2)
    return r, w


async def _pipe(src, dst):
    try:
        while data := await src.read(65536):
            dst.write(data)
            await dst.drain()
    except Exception:  # noqa: BLE001
        pass
    finally:
        try:
            dst.close()
        except Exception:  # noqa: BLE001
            pass


async def _http_connect(p: Proxy, host: str, port: int):
    r, w = await asyncio.open_connection(p.host, p.port)
    auth = ""
    if p.username:
        token = base64.b64encode(f"{p.username}:{p.password}".encode()).decode()
        auth = f"Proxy-Authorization: Basic {token}\r\n"
    w.write(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n{auth}\r\n".encode())
    await w.drain()
    head = await r.readuntil(b"\r\n\r\n")
    parts = head.split(b" ", 2)
    if len(parts) < 2 or parts[1] != b"200":
        raise ConnectionError(f"HTTP proxy CONNECT failed: {head[:60]!r}")
    return r, w


async def upstream_connect(p: Proxy, host: str, port: int):
    """Туннель к host:port через upstream-прокси (SOCKS5 или HTTP CONNECT) с авторизацией."""
    return await (_socks5_connect if p.type == "socks5" else _http_connect)(p, host, port)


class UpstreamRelay:
    def __init__(self, upstream: Proxy):
        self.upstream = upstream
        self.server: asyncio.base_events.Server | None = None
        self.port = 0

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, cr: asyncio.StreamReader, cw: asyncio.StreamWriter):
        try:
            head = await cr.readuntil(b"\r\n\r\n")
            first = head.split(b"\r\n", 1)[0].decode("latin1")
            method, target, version = first.split(" ", 2)
            if method == "CONNECT":
                host, _, port = target.rpartition(":")
                ur, uw = await upstream_connect(self.upstream, host, int(port))
                cw.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await cw.drain()
            elif self.upstream.type != "socks5":  # обычный HTTP: отдаём upstream-прокси, добавив авторизацию
                ur, uw = await asyncio.open_connection(self.upstream.host, self.upstream.port)
                lines = head.split(b"\r\n")
                if self.upstream.username:
                    token = base64.b64encode(f"{self.upstream.username}:{self.upstream.password}".encode())
                    lines.insert(1, b"Proxy-Authorization: Basic " + token)
                uw.write(b"\r\n".join(lines))
                await uw.drain()
            else:  # обычный HTTP через абсолютный URI
                from urllib.parse import urlsplit
                u = urlsplit(target)
                host, port = u.hostname, u.port or 80
                ur, uw = await _socks5_connect(self.upstream, host, port)
                path = (u.path or "/") + (f"?{u.query}" if u.query else "")
                lines = head.split(b"\r\n")
                lines[0] = f"{method} {path} {version}".encode("latin1")
                uw.write(b"\r\n".join(lines))
                await uw.drain()
            await asyncio.gather(_pipe(cr, uw), _pipe(ur, cw))
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                cw.close()
            except Exception:  # noqa: BLE001
                pass


Socks5Relay = UpstreamRelay  # старое имя
