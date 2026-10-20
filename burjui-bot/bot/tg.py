"""Минимальный клиент Telegram Bot API на стандартной библиотеке."""
import json
import logging
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

log = logging.getLogger(__name__)


class TelegramError(Exception):
    pass


class Telegram:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}/"

    def call(self, method: str, _timeout: float = 30, **params):
        params = {k: v for k, v in params.items() if v is not None}
        files = params.pop("_files", None)
        if files:
            body, ctype = _multipart(params, files)
        else:
            body, ctype = json.dumps(params).encode(), "application/json"
        req = urllib.request.Request(self.base + method, data=body, headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=_timeout) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            data = json.load(e)
        if not data.get("ok"):
            desc = data.get("description", "")
            if data.get("error_code") == 429:
                time.sleep(data.get("parameters", {}).get("retry_after", 1))
                return self.call(method, _timeout, **params, **({"_files": files} if files else {}))
            if "message is not modified" in desc:
                return None
            raise TelegramError(f"{method}: {desc}")
        return data["result"]

    # Удобные обёртки
    def send(self, chat_id, text, markup=None, **kw):
        return self.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                         reply_markup=markup, link_preview_options={"is_disabled": True}, **kw)

    def edit(self, chat_id, message_id, text, markup=None):
        return self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text,
                         parse_mode="HTML", reply_markup=markup, link_preview_options={"is_disabled": True})

    def answer_callback(self, callback_id, text=None):
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_id, text=text)
        except TelegramError as e:  # кнопка устарела — не страшно
            log.debug("answerCallbackQuery: %s", e)

    def send_photos(self, chat_id, photos):
        """photos: список (file_id или Path). Возвращает file_id отправленных фото по порядку."""
        media, files = [], {}
        for i, p in enumerate(photos[:10]):
            if isinstance(p, Path):
                files[f"p{i}"] = p
                media.append({"type": "photo", "media": f"attach://p{i}"})
            else:
                media.append({"type": "photo", "media": p})
        if len(media) == 1:
            if files:
                res = [self.call("sendPhoto", chat_id=chat_id, _files={"photo": files["p0"]}, _timeout=120)]
            else:
                res = [self.call("sendPhoto", chat_id=chat_id, photo=media[0]["media"])]
        else:
            res = self.call("sendMediaGroup", chat_id=chat_id, media=media, _files=files or None, _timeout=120)
        return [m["photo"][-1]["file_id"] for m in res]


def _multipart(params: dict, files: dict):
    boundary = uuid.uuid4().hex
    out = bytearray()
    for k, v in params.items():
        if not isinstance(v, (str, int, float)):
            v = json.dumps(v)
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for name, path in files.items():
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{path.name}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode()
        out += path.read_bytes() + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"
