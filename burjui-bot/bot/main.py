"""Запуск: python -m bot.main"""
import logging
import time

from . import config
from .handlers import Bot
from .storage import Storage
from .tg import Telegram, TelegramError

COMMANDS = [
    {"command": "menu", "description": "Главное меню"},
    {"command": "halls", "description": "Залы, фото и цены"},
    {"command": "book", "description": "Рассчитать цену и забронировать"},
    {"command": "admin", "description": "Написать администратору"},
]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("bot")
    if not config.BOT_TOKEN:
        raise SystemExit("Не задан BOT_TOKEN (см. .env.example)")
    tg = Telegram(config.BOT_TOKEN)
    assistant = None
    if config.ANTHROPIC_API_KEY:
        from .ai import Assistant
        assistant = Assistant()
        log.info("ИИ включён: %s", config.AI_MODEL)
    else:
        log.info("ANTHROPIC_API_KEY не задан — свободный текст разбирается по ключевым словам")
    if not config.ADMIN_CHAT_ID:
        log.warning("ADMIN_CHAT_ID не задан — заявки никуда не уйдут. Добавьте бота в группу и напишите /id")
    me = tg.call("getMe")
    tg.call("setMyCommands", commands=COMMANDS)
    tg.call("deleteWebhook")
    log.info("Бот @%s запущен", me["username"])

    bot = Bot(tg, Storage(), assistant)
    offset = None
    while True:
        try:
            updates = tg.call("getUpdates", 70, offset=offset, timeout=50,
                              allowed_updates=["message", "callback_query"])
        except (TelegramError, OSError) as e:
            log.warning("getUpdates: %s", e)
            time.sleep(3)
            continue
        for upd in updates or []:
            offset = upd["update_id"] + 1
            bot.handle(upd)


if __name__ == "__main__":
    main()
