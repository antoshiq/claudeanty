import asyncio
from pathlib import Path
from typing import Optional

from camoufox.async_api import AsyncCamoufox
from camoufox.fingerprints import generate_fingerprint

from ..models import Profile, Proxy
from .base import Engine, RunningSession


def make_camoufox_fingerprint(os_name: str) -> dict:
    """Генерируется один раз при создании профиля и хранится в БД -> отпечаток стабилен."""
    return generate_fingerprint(os=os_name if os_name in ("windows", "macos", "linux") else "windows")


class CamoufoxEngine(Engine):
    name = "camoufox"

    async def start(self, profile: Profile, proxy: Optional[Proxy], user_data_dir: str,
                    headless: bool = False) -> RunningSession:
        opts = dict(
            headless=headless,
            persistent_context=True,
            user_data_dir=user_data_dir,
            humanize=True,
            locale=profile.locale,
            # WebRTC: off — отключён; proxy — Camoufox подменяет WebRTC-IP на IP прокси (geoip); real — как есть
            block_webrtc=profile.webrtc == "off",
        )
        cf_fp = profile.fingerprint.get("camoufox")
        if cf_fp:
            opts["fingerprint"] = cf_fp
            opts["i_know_what_im_doing"] = True  # свой отпечаток нужен для стабильности между запусками
        else:
            opts["os"] = profile.os if profile.os in ("windows", "macos", "linux") else "windows"

        if proxy:
            px = {"server": f"{proxy.type}://{proxy.host}:{proxy.port}"}
            if proxy.username:
                px["username"] = proxy.username
                px["password"] = proxy.password
            opts["proxy"] = px
            opts["geoip"] = bool(profile.auto_geo) or profile.webrtc == "proxy"

        # расширения Firefox: распакованные папки с manifest.json
        addons = [str(Path(e)) for e in profile.extensions if Path(e, "manifest.json").exists()]
        if addons:
            opts["addons"] = addons

        cm = AsyncCamoufox(**opts)
        ctx = await cm.__aenter__()

        async def close_cm():
            await cm.__aexit__(None, None, None)

        sess = RunningSession(profile.id, self.name, ctx, ws_endpoint="", cleanups=[close_cm])
        if not headless and profile.start_urls:
            from .chromium import open_start_urls
            asyncio.ensure_future(open_start_urls(ctx, profile.start_urls))
        return sess
