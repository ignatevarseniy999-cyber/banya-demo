"""Настройки бота. Всё берётся из переменных окружения (или файла .env рядом с проектом)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MEDIA_DIR = ROOT / "media"


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
# Чат администраторов (группа), куда бот пишет заявки. Узнать id: добавить бота в группу и написать /id
ADMIN_CHAT_ID = int(os.environ["ADMIN_CHAT_ID"]) if os.environ.get("ADMIN_CHAT_ID") else None
# Ключ Claude API. Без него бот работает на кнопках, а свободный текст разбирает по ключевым словам
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
AI_MODEL = os.environ.get("AI_MODEL", "claude-opus-5-5")
# Ссылка на политику обработки персональных данных (152-ФЗ)
PRIVACY_URL = os.environ.get("PRIVACY_URL", "")
DB_PATH = Path(os.environ.get("DB_PATH", str(ROOT / "bot.sqlite3")))
TIMEZONE = "Europe/Moscow"
