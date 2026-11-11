# claudeAnty — свой антидетект-браузер

Менеджер профилей поверх Chromium (Playwright) и Camoufox (Firefox). Профили, прокси, мобильный режим,
локальный API для автоматизации, импорт/экспорт cookies.

## Установка

```powershell
pip install -r requirements.txt
python -m playwright install chromium
python -m camoufox fetch        # только если нужны Camoufox-профили
python run.py
```

Данные хранятся в `data/` (SQLite + папка на каждый профиль). Локальный API: `http://127.0.0.1:3001`
(доступен только с вашего компьютера).

## Что умеет

| Функция | Как |
|---|---|
| Профили ПК | Windows / macOS / Linux, движок Chromium или Camoufox |
| Профили «телефон» | Android-устройства (Galaxy, Pixel, Xiaomi, OnePlus…), только Chromium |
| Прокси | HTTP / HTTPS / SOCKS5 (в том числе SOCKS5 с логином через локальный relay), проверка, автоопределение страны/таймзоны |
| Авто-гео | Таймзона, язык и геолокация подставляются по IP прокси |
| Отпечаток | Генерируется один раз при создании профиля и не меняется при перезапуске |
| Cookies | Импорт/экспорт JSON (EditThisCookie) и Netscape, в том числе для остановленного профиля |
| Организация | Папки, теги, заметки, поиск, массовое создание |

## Защита отпечатка (Chromium)

**Всё, что Chromium умеет нативно, задаётся через CDP, а не JS** — подделку нечем обнаружить:
UA и Client Hints (версия = реальный движок), платформа, языки, ядра CPU, размер экрана, touch и DPR телефона,
таймзона, геолокация, тип сети и датчики телефона. Playwright сам ничего не эмулирует и запускает Chrome
без своих служебных флагов (`--enable-automation`, `--no-sandbox`, отключённые фичи и т.д.).

**Оставшиеся JS-подмены неотличимы от нативных** (память, WebGL, canvas, audio, шрифты, медиаустройства,
WebGPU): без `prototype`, с правильными `name`/`length`, «Illegal invocation» на прототипе, без кадров в стеке
ошибок, `toString` верен из любого контекста (в т.ч. iframe), никаких глобальных маркеров. Шум canvas и audio
стабилен (повторный вызов даёт то же самое) и не трогает однотонные заливки.

Подмена работает и в Web Worker / SharedWorker / ServiceWorker, и в iframe.

Проверено CreepJS (ПК, Windows): **headless 0%, stealth 0%, лжи не найдено**; like headless 25% — это
Android-only API и светлая тема, как у обычного Chrome на ПК. Телефон: headless 0%, stealth 0%, like headless 38%
(у сборки Chrome для ПК нет Android-API вроде ContactsManager — предел эмуляции телефона на ПК).

Встроенная проверка профиля (кнопка с отпечатком пальца) — 20 проверок, включая детекты подмены как в CreepJS.
«Открыть сайты проверки» открывает CreepJS, pixelscan и browserleaks в профиле.

Ограничение: когда профилем управляет внешний скрипт через API (Playwright/Puppeteer), сам скрипт может оставить
свои следы — это уже зависит от него.

## Мобильный режим

Подменяются: User-Agent и Client Hints (Android, модель), размер экрана и DPR, touch, `navigator.platform`
(`Linux armv81`), GPU (Adreno/Mali), `navigator.connection`, Battery API, события датчиков.

**Ограничения** (честно): подмена идёт через CDP и JS, поэтому сильный антифрод может её обнаружить.
Шрифты и часть аппаратного поведения остаются компьютерными — полностью скрыть, что это ПК, нельзя
ни в одном антидетект-браузере. Режим не заменяет реальное устройство и мобильный прокси.

Camoufox (Firefox) для мобильных профилей не используется: на Android почти все сидят в Chrome,
Firefox-«телефон» выглядел бы подозрительно.

## API

Все методы — на `http://127.0.0.1:3001`.

| Метод | Путь | Описание |
|---|---|---|
| GET | `/profiles`, `/profiles/{id}` | список / один профиль |
| POST | `/profiles` | создать (`{"name","kind":"mobile","device":"pixel_8","proxy_id":1}`) |
| POST | `/profiles/bulk` | массово: `{"template":{...},"count":10,"name_prefix":"Acc","proxies":["socks5://u:p@h:1080"]}` |
| PATCH / DELETE | `/profiles/{id}` | изменить / удалить |
| GET | `/profiles/{id}/start?headless=false` | запуск; для Chromium вернёт `ws_endpoint` |
| GET | `/profiles/{id}/stop` | остановка |
| GET / POST | `/profiles/{id}/cookies` | экспорт (`?format=json\|netscape`) / импорт `{"cookies":"..."}` |
| GET / POST / DELETE | `/proxies`, `/proxies/{id}` | список / добавить (`{"url":"..."}`) / удалить |
| POST | `/proxies/{id}/check` | проверить прокси и заполнить гео |
| GET | `/devices` | доступные модели телефонов |
| POST | `/profiles/{id}/check` | проверка отпечатка (список проверок ✓/✗) |
| POST | `/profiles/{id}/warmup` | прогрев cookies |
| POST | `/sync/start`, `/sync/stop`; GET `/sync` | синхронизатор: `{"leader": 1, "followers": [2, 3]}` |

### Playwright (Python)

```python
import httpx
from playwright.sync_api import sync_playwright

r = httpx.get("http://127.0.0.1:3001/profiles/1/start").json()
with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp(r["ws_endpoint"])
    page = browser.contexts[0].new_page()
    page.goto("https://browserleaks.com")
httpx.get("http://127.0.0.1:3001/profiles/1/stop")
```

### Puppeteer

```js
const r = await (await fetch("http://127.0.0.1:3001/profiles/1/start")).json();
const browser = await puppeteer.connect({ browserWSEndpoint: r.ws_endpoint });
```

### Selenium

```python
opts = webdriver.ChromeOptions()
opts.debugger_address = f"127.0.0.1:{r['debug_port']}"
driver = webdriver.Chrome(options=opts)   # нужен chromedriver, подходящий версии Chromium
```

**Camoufox через API:** профиль запускается, но внешнего подключения по CDP нет (это Firefox,
`ws_endpoint` пустой). Управлять им можно только из окна приложения.

## Проверка отпечатка

Откройте в профиле: `browserleaks.com` (WebRTC, Canvas, WebGL), `pixelscan.net`,
`abrahamjuliot.github.io/creepjs`. IP должен совпадать с прокси, без WebRTC-утечки, таймзона и язык — с IP.

## Структура

`app/fingerprints` — генератор и JS-инъекции · `app/engines` — Chromium/Camoufox ·
`app/proxy` — проверка и relay · `app/manager.py` — жизненный цикл профилей ·
`app/api` — FastAPI · `app/gui` — PySide6.
