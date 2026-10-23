"""Настройки из файла .env (или переменных окружения)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "да", "on")


@dataclass
class Config:
    bot_token: str
    allowed_chat_ids: set[int]
    check_interval: int
    avito_region: str
    db_path: Path
    browser_headless: bool
    browser_channel: str
    browser_profile: Path
    proxy: str


def load_config() -> Config:
    load_env(ROOT / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "Не задан TELEGRAM_BOT_TOKEN. Скопируйте .env.example в .env и впишите токен бота."
        )
    ids = {
        int(x)
        for x in os.environ.get("ALLOWED_CHAT_IDS", "").replace(" ", "").split(",")
        if x.lstrip("-").isdigit()
    }
    return Config(
        bot_token=token,
        allowed_chat_ids=ids,
        check_interval=max(20, int(os.environ.get("CHECK_INTERVAL", "60"))),
        avito_region=os.environ.get("AVITO_REGION", "rossiya").strip() or "rossiya",
        db_path=ROOT / os.environ.get("DB_PATH", "data.db"),
        browser_headless=_flag("BROWSER_HEADLESS", False),
        browser_channel=os.environ.get("BROWSER_CHANNEL", "").strip(),
        browser_profile=ROOT / os.environ.get("BROWSER_PROFILE", "browser_profile"),
        proxy=os.environ.get("PROXY", "").strip(),
    )
