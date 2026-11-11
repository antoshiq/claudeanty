from __future__ import annotations
from typing import Optional, Literal
from pydantic import BaseModel, Field


class Proxy(BaseModel):
    id: Optional[int] = None
    type: Literal["http", "https", "socks5"] = "http"
    host: str
    port: int
    username: str = ""
    password: str = ""
    country: str = ""
    timezone: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    ip: str = ""
    status: str = ""  # "" не проверен | ok | fail
    error: str = ""
    ip_type: str = ""  # residential | mobile | hosting | proxy (по ip-api)
    tcp_os: str = ""  # ОС выходного узла по TCP/IP-отпечатку (p0f, browserleaks): Windows, Linux, Android…
    mtu: int = 0  # MTU соединения: 1500 — обычный Ethernet, меньше — туннель (VPN/прокси)
    change_ip_url: str = ""  # ссылка смены IP мобильного прокси (открывается перед запуском профиля)

    @classmethod
    def parse(cls, line: str) -> "Proxy":
        """type://user:pass@host:port | host:port | host:port:user:pass
        После прокси через « | » можно указать ссылку смены IP: socks5://u:p@h:1080 | https://…/changeip"""
        line = line.strip()
        change = ""
        if "|" in line:
            line, change = (x.strip() for x in line.split("|", 1))
        ptype = "http"
        if "://" in line:
            ptype, line = line.split("://", 1)
        user = pwd = ""
        if "@" in line:
            cred, hostport = line.rsplit("@", 1)
            user, _, pwd = cred.partition(":")
        else:
            parts = line.split(":")
            if len(parts) == 4:
                hostport, user, pwd = f"{parts[0]}:{parts[1]}", parts[2], parts[3]
            else:
                hostport = line
        host, _, port = hostport.rpartition(":")
        return cls(type=ptype, host=host, port=int(port), username=user, password=pwd, change_ip_url=change)

    def url(self) -> str:
        auth = f"{self.username}:{self.password}@" if self.username else ""
        return f"{self.type}://{auth}{self.host}:{self.port}"


class Profile(BaseModel):
    id: Optional[int] = None
    name: str
    kind: Literal["desktop", "mobile"] = "desktop"
    engine: Literal["chromium", "camoufox"] = "chromium"
    os: Literal["windows", "macos", "linux", "android"] = "windows"
    device: str = ""  # ключ пресета телефона для mobile
    proxy_id: Optional[int] = None
    folder: str = ""
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    auto_geo: bool = True
    locale: str = "en-US"
    timezone: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    fingerprint: dict = Field(default_factory=dict)
    # WebRTC: proxy — только через прокси (реальный IP не утекает), off — WebRTC отключён, real — как есть
    webrtc: Literal["proxy", "off", "real"] = "proxy"
    status: str = ""  # метка: new | warming | ready | work | ban | ""
    start_urls: list[str] = Field(default_factory=list)
    bookmarks: list[dict] = Field(default_factory=list)  # [{"title", "url"}]
    extensions: list[str] = Field(default_factory=list)  # пути к распакованным расширениям
    last_check: dict = Field(default_factory=dict)  # результат последней проверки отпечатка
    fp_history: list = Field(default_factory=list)  # хэши отпечатка по проверкам (стабильность между запусками)
    last_external: dict = Field(default_factory=dict)  # последний отчёт внешних детекторов (pixelscan, CreepJS…)


class Account(BaseModel):
    """Логин/пароль/2FA сайта в профиле. В базе password, totp и notes зашифрованы (DPAPI, см. crypto.py)."""
    id: Optional[int] = None
    profile_id: int
    service: str = ""  # название: Google, Facebook…
    url: str = ""  # адрес входа
    login: str = ""
    password: str = ""
    totp: str = ""  # секрет 2FA: base32 («JBSW Y3DP…») или ссылка otpauth://totp/…
    notes: str = ""  # резервные коды и т.п.


STATUSES = {
    "": ("Без метки", "#6e7681"),
    "new": ("Новый", "#8b949e"),
    "warming": ("Прогрев", "#d29922"),
    "ready": ("Готов", "#3fb950"),
    "work": ("В работе", "#2f81f7"),
    "ban": ("Бан", "#f85149"),
}
