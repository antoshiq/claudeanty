from .paths import APP_DIR, DATA_DIR

ROOT = APP_DIR
PROFILES_DIR = DATA_DIR / "profiles"
DB_PATH = DATA_DIR / "claudeanty.sqlite"
API_HOST = "127.0.0.1"
from . import settings as _settings

API_PORT = int(_settings.get("api_port"))

DATA_DIR.mkdir(exist_ok=True)
PROFILES_DIR.mkdir(exist_ok=True)
