import asyncio
import json
import logging
import secrets
import uuid
from pathlib import Path
from typing import Optional

import httpx
from playwright.async_api import async_playwright

from ..fingerprints.generator import enrich
from ..models import Profile, Proxy
from ..proxy.relay import UpstreamRelay
from .base import Engine, RunningSession, free_port
from .cdp_client import CDPClient
from .chrome_version import apply_version, engine_kind, engine_version, executable

log = logging.getLogger("claudeanty.chromium")
_INJECT = Path(__file__).parent.parent / "fingerprints" / "inject"
_STEALTH = (_INJECT / "stealth.js").read_text(encoding="utf-8")
_WORKER = (_INJECT / "worker.js").read_text(encoding="utf-8")


def build_fp(profile: Profile) -> dict:
    """Отпечаток в том виде, в каком он уходит в браузер: реальная версия Chrome + производные поля."""
    fp = apply_version(enrich(profile.fingerprint), engine_version())
    fp["languages"] = [profile.locale, profile.locale.split("-")[0]]
    fp["webrtc"] = profile.webrtc
    return fp


def ua_override(fp: dict) -> dict:
    ch = fp["ch"]
    return {
        "userAgent": fp["user_agent"], "platform": fp["platform"], "acceptLanguage": ",".join(fp["languages"]),
        "userAgentMetadata": {
            "brands": ch["brands"], "fullVersionList": ch["full_version_list"], "fullVersion": ch["full_version"],
            "platform": ch["platform"], "platformVersion": ch["platform_version"], "architecture": ch["architecture"],
            "model": ch["model"], "mobile": ch["mobile"], "bitness": ch["bitness"], "wow64": False,
        },
    }


def page_commands(profile: Profile, fp: dict) -> list[tuple[str, dict]]:
    """Нативная эмуляция через CDP для каждой страницы — вместо JS-подмен, которые можно обнаружить."""
    cmds = [
        ("Emulation.setUserAgentOverride", ua_override(fp)),           # UA, Client Hints, платформа, языки
        ("Emulation.setLocaleOverride", {"locale": profile.locale}),
        ("Emulation.setHardwareConcurrencyOverride", {"hardwareConcurrency": int(fp["cores"])}),
    ]
    if profile.timezone:
        cmds.append(("Emulation.setTimezoneOverride", {"timezoneId": profile.timezone}))
    if profile.lat is not None and profile.lon is not None:
        cmds.append(("Emulation.setGeolocationOverride", {"latitude": profile.lat, "longitude": profile.lon, "accuracy": 35}))
    sc, vp = fp["screen"], fp["viewport"]
    if fp["mobile"]:
        cmds += [
            ("Emulation.setDeviceMetricsOverride", {
                "width": vp["width"], "height": vp["height"], "deviceScaleFactor": sc["dpr"], "mobile": True,
                "screenWidth": sc["width"], "screenHeight": sc["height"],
                "screenOrientation": {"type": "portraitPrimary", "angle": 0}}),
            ("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5}),
            ("Network.enable", {}),
            ("Network.emulateNetworkConditions", {"offline": False, "latency": 0, "downloadThroughput": -1,
                                                  "uploadThroughput": -1, "connectionType": "cellular4g"}),
            ("DeviceOrientation.setDeviceOrientationOverride", {"alpha": 0, "beta": 62, "gamma": 1}),
        ]
    else:
        # окно и viewport настоящие (меняются вместе с окном), подменяется только размер экрана
        cmds.append(("Emulation.setDeviceMetricsOverride", {
            "width": 0, "height": 0, "deviceScaleFactor": 0, "mobile": False,
            "screenWidth": sc["width"], "screenHeight": sc["height"]}))
    return cmds


def write_bookmarks(user_data_dir: str, bookmarks: list[dict]):
    """Закладки профиля на панель закладок Chromium (существующие пользовательские сохраняются)."""
    if not bookmarks:
        return
    default = Path(user_data_dir) / "Default"
    default.mkdir(parents=True, exist_ok=True)
    path = default / "Bookmarks"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        data = {"roots": {}, "version": 1}
    roots = data.setdefault("roots", {})

    def folder(name, guid):
        return {"children": [], "date_added": "13300000000000000", "date_modified": "0", "guid": guid,
                "name": name, "type": "folder"}

    bar = roots.setdefault("bookmark_bar", folder("Панель закладок", "0bc5d13f-2cba-5d74-951f-3f233fe6c908"))
    roots.setdefault("other", folder("Другие закладки", "82b081ec-3dd3-529c-8475-ab6c344590dd"))
    roots.setdefault("synced", folder("Мобильные закладки", "4cf2e351-0e85-532b-bb37-df045d8f8d0f"))
    ours = {b["url"] for b in bookmarks}
    keep = [c for c in bar.get("children", []) if c.get("url") not in ours]
    bar["children"] = [{"date_added": "13300000000000000", "guid": str(uuid.uuid4()), "name": b.get("title") or b["url"],
                        "type": "url", "url": b["url"]} for b in bookmarks] + keep
    # id должны быть уникальны — перенумеровываем всё дерево
    counter = [0]

    def renum(node):
        counter[0] += 1
        node["id"] = str(counter[0])
        for ch in node.get("children", []):
            renum(ch)

    for key in ("bookmark_bar", "other", "synced"):
        renum(roots[key])
    data.pop("checksum", None)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # показывать панель закладок
    prefs_path = default / "Preferences"
    try:
        prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        prefs = {}
    prefs.setdefault("bookmark_bar", {})["show_on_all_tabs"] = True
    prefs_path.write_text(json.dumps(prefs), encoding="utf-8")


async def open_start_urls(ctx, urls: list[str]):
    urls = [u.strip() for u in urls if u.strip()]
    if not urls:
        return
    for i, url in enumerate(urls):
        if "://" not in url:
            url = "https://" + url
        try:
            page = ctx.pages[0] if i == 0 and ctx.pages and ctx.pages[0].url in ("about:blank", "") else await ctx.new_page()
            await page.goto(url, wait_until="commit", timeout=30000)
        except Exception as e:  # noqa: BLE001
            log.warning("start url %s: %s", url, e)


async def load_extensions(ctx, paths: list[str]):
    """Распакованные расширения в Google Chrome через CDP (браузерная сессия Playwright идёт по pipe)."""
    try:
        cdp = await ctx.browser.new_browser_cdp_session()
    except Exception as e:  # noqa: BLE001
        log.warning("extensions: нет CDP-сессии браузера: %s", e)
        return
    for path in paths:
        try:
            await cdp.send("Extensions.loadUnpacked", {"path": path})
        except Exception as e:  # noqa: BLE001
            log.warning("extension %s: %s", path, e)
    await cdp.detach()


class ChromiumEngine(Engine):
    name = "chromium"

    async def start(self, profile: Profile, proxy: Optional[Proxy], user_data_dir: str,
                    headless: bool = False) -> RunningSession:
        fp = build_fp(profile)
        pw = await async_playwright().start()
        cleanups = [pw.stop]

        # --- прокси: без авторизации — напрямую, с логином/паролем — через локальный relay ---
        proxy_server = None
        if proxy:
            if proxy.username:
                relay = UpstreamRelay(proxy)
                port = await relay.start()
                cleanups.append(relay.stop)
                proxy_server = f"http://127.0.0.1:{port}"
            else:
                proxy_server = f"{proxy.type}://{proxy.host}:{proxy.port}"

        # --- WebRTC ---
        webrtc_args = []
        if profile.webrtc in ("proxy", "off"):
            policy = "disable_non_proxied_udp" if proxy_server else "default_public_interface_only"
            webrtc_args = [f"--force-webrtc-ip-handling-policy={policy}", f"--webrtc-ip-handling-policy={policy}"]

        # Только свои флаги, как у обычного запуска Chrome. Служебные флаги Playwright убраны все:
        # --enable-automation, --no-sandbox, --disable-popup-blocking, --disable-features=…(десяток фич),
        # --disable-field-trial-config, --force-color-profile и т.д. — по ним браузер отличается от настоящего
        # (например, Notification.permission становится «denied»).
        debug_port = free_port()
        vp = fp["viewport"]
        args = [
            f"--user-data-dir={user_data_dir}",
            "--remote-debugging-pipe",  # канал Playwright
            f"--remote-debugging-port={debug_port}",  # канал нашего CDP-клиента и внешнего API
            "--no-first-run", "--no-default-browser-check",
            "--disable-infobars",  # плашка «Chrome for Testing»
            "--disable-blink-features=AutomationControlled",
            *webrtc_args,
            f"--window-size={vp['width'] + (16 if fp['mobile'] else 0)},{vp['height'] + 90}",
            # CalculateNativeWinOcclusion: без этого Chromium может перестать рисовать, когда окно встроено во
            # вкладку приложения. IsolateSandboxedIframes: iframe с sandbox (srcdoc) иначе получает свой процесс
            # без паузы на старте — скрипт в нём успевает прочитать системные таймзону, ядра и язык до эмуляции.
            # В процессе страницы такой iframe наследует всю эмуляцию; из JS разница процессов не видна.
            "--disable-features=CalculateNativeWinOcclusion,IsolateSandboxedIframes",
            f"--lang={profile.locale}",
        ]
        if proxy_server:
            args += [f"--proxy-server={proxy_server}", "--proxy-bypass-list=<-loopback>"]
        if headless:
            # UA по умолчанию у headless — «HeadlessChrome»; CDP-подмена не всегда успевает в SharedWorker,
            # поэтому задаём UA и флагом (значение то же, что в отпечатке)
            args.append(f"--user-agent={fp['user_agent']}")
            args += ["--headless", "--hide-scrollbars", "--mute-audio", "--enable-unsafe-swiftshader",
                     # как у обычного ПК: мышь с наведением (иначе headless сообщает «нет указателя»)
                     "--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4"]
        exts = [str(Path(e)) for e in profile.extensions if Path(e, "manifest.json").exists()]
        chrome = engine_kind() == "chrome"
        if exts:
            if chrome:
                # Google Chrome 137+ игнорирует --load-extension: расширения ставятся после запуска
                # командой CDP Extensions.loadUnpacked (доступна только по pipe и с этим флагом)
                args.append("--enable-unsafe-extension-debugging")
            else:
                joined = ",".join(exts)
                args += [f"--load-extension={joined}", f"--disable-extensions-except={joined}"]
        args.append("about:blank")

        if not headless:
            write_bookmarks(user_data_dir, profile.bookmarks)

        # Playwright только запускает браузер и управляет им — без эмуляции (no_viewport, без UA/locale/tz):
        # иначе его настройки перезаписали бы наши. Всю эмуляцию нативно делает CDPClient ниже.
        kwargs = dict(
            user_data_dir=user_data_dir,
            headless=headless,
            args=args,
            ignore_default_args=True,
            no_viewport=True,
        )
        exe = executable()
        if exe:
            kwargs["executable_path"] = exe  # установленный Google Chrome

        ctx = await pw.chromium.launch_persistent_context(**kwargs)
        if exts and chrome:
            await load_extensions(ctx, exts)
        # метка нашего скрипта: по ней подмена вырезает свои кадры из стека ошибок
        fp["token"] = "ca" + secrets.token_hex(6)
        fp_json = json.dumps(fp)
        await ctx.add_init_script(_STEALTH.replace("__FP__", fp_json) + f"\n//# sourceURL={fp['token']}\n")

        ws = ""
        try:
            async with httpx.AsyncClient() as c:
                ws = (await c.get(f"http://127.0.0.1:{debug_port}/json/version", timeout=10)).json().get("webSocketDebuggerUrl", "")
        except Exception:  # noqa: BLE001
            pass

        if ws:
            client = CDPClient(ws, page_commands(profile, fp), [("Network.setUserAgentOverride", ua_override(fp))],
                               _WORKER.replace("__FP__", fp_json) + f"\n//# sourceURL={fp['token']}\n")
            try:
                await client.start()
                # Геолокацию НЕ разрешаем заранее: Browser.grantPermissions отклоняет все остальные разрешения
                # (Notification.permission = «denied» — признак headless), а «разрешено всем сайтам» нетипично.
                # Сайт спросит, как в обычном Chrome; координаты придут из Emulation.setGeolocationOverride.
                await client.ready()
                cleanups.insert(0, client.stop)
            except Exception as e:  # noqa: BLE001
                log.warning("CDP client: %s", e)

        sess = RunningSession(profile.id, self.name, ctx, ws_endpoint=ws, debug_port=debug_port, cleanups=cleanups)
        if not headless:
            asyncio.ensure_future(open_start_urls(ctx, profile.start_urls))
        return sess
