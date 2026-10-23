"""Минимальный клиент Telegram Bot API (без сторонних библиотек)."""
from __future__ import annotations

import json
import logging
import time

import requests

log = logging.getLogger(__name__)


class Telegram:
    def __init__(self, token: str, proxy: str = ""):
        self.base = f"https://api.telegram.org/bot{token}/"
        self.session = requests.Session()
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

    def call(self, method: str, http_timeout: int = 40, **params):
        for attempt in range(3):
            try:
                resp = self.session.post(self.base + method, data=params, timeout=http_timeout)
                data = resp.json()
            except (requests.RequestException, ValueError) as exc:
                log.warning("Telegram %s: %s", method, exc)
                time.sleep(2 + attempt * 3)
                continue
            if data.get("ok"):
                return data["result"]
            retry = data.get("parameters", {}).get("retry_after")
            if retry:  # слишком много сообщений подряд — Telegram просит подождать
                time.sleep(retry + 1)
                continue
            raise TelegramError(data.get("description", "unknown error"))
        raise TelegramError(f"{method}: нет связи с Telegram")

    def get_updates(self, offset: int | None):
        params = {"timeout": 30, "allowed_updates": json.dumps(["message"])}
        if offset is not None:
            params["offset"] = offset
        return self.call("getUpdates", http_timeout=45, **params)

    def send_message(self, chat_id: int, text: str, button_url: str | None = None) -> None:
        params = dict(
            chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview="true"
        )
        if button_url:
            params["reply_markup"] = _button(button_url)
        self.call("sendMessage", **params)

    def send_photo(self, chat_id: int, photo: str, caption: str, button_url: str | None = None):
        params = dict(chat_id=chat_id, photo=photo, caption=caption, parse_mode="HTML")
        if button_url:
            params["reply_markup"] = _button(button_url)
        self.call("sendPhoto", **params)


class TelegramError(Exception):
    pass


def _button(url: str) -> str:
    return json.dumps({"inline_keyboard": [[{"text": "Открыть объявление", "url": url}]]})
