"""Запуск: python monitor.py"""
import logging
import sys

from carmon.app import App
from carmon.config import load_config
from carmon.db import Database
from carmon.fetcher import Fetcher
from carmon.telegram import Telegram


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    config = load_config()
    fetcher = Fetcher(
        config.browser_profile,
        headless=config.browser_headless,
        channel=config.browser_channel,
        proxy=config.proxy,
    )
    app = App(
        config,
        Database(config.db_path),
        fetcher,
        Telegram(config.bot_token),
        Telegram(config.bot_token),
    )
    app.run()


if __name__ == "__main__":
    main()
