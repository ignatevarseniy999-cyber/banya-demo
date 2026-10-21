"""Запуск бота в MAX: python -m bot.max_main"""
import logging
import time

from . import config
from .handlers import Bot
from .main import COMMANDS
from .max_api import Max, MaxError
from .storage import Storage


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("bot")
    if not config.MAX_TOKEN:
        raise SystemExit("Не задан MAX_TOKEN (см. .env.example)")
    mx = Max(config.MAX_TOKEN, config.MAX_API_URL, config.MAX_CA_FILE, config.ADMIN_CHAT_ID)
    assistant = None
    if config.ANTHROPIC_API_KEY:
        from .ai import Assistant
        assistant = Assistant()
        log.info("ИИ включён: %s", config.AI_MODEL)
    if not config.ADMIN_CHAT_ID:
        log.warning("ADMIN_CHAT_ID не задан — добавьте бота в группу администраторов, он пришлёт id чата")
    me = mx.request("GET", "/me")
    try:
        mx.set_commands(COMMANDS)
    except MaxError as e:
        log.warning("Команды не установлены: %s", e)
    log.info("Бот %s (%s) запущен в MAX", me.get("name"), me.get("username"))

    bot = Bot(mx, Storage(), assistant)
    marker = None
    while True:
        try:
            updates, marker = mx.get_updates(marker)
        except (MaxError, OSError) as e:
            log.warning("updates: %s", e)
            time.sleep(3)
            continue
        for upd in updates:
            converted = mx.convert(upd)
            if converted:
                bot.handle(converted)


if __name__ == "__main__":
    main()
