"""Клиент Bot API мессенджера MAX (https://dev.max.ru/docs-api) на стандартной библиотеке.

Снаружи выглядит так же, как Telegram-клиент из tg.py: те же методы send / edit / send_photos и т.д.,
а обновления MAX переводятся в формат Telegram. Поэтому логика бота в handlers.py общая для обоих.
"""
import html
import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .channel import ChannelError

log = logging.getLogger(__name__)


class MaxError(ChannelError):
    pass


class Max:
    name = "max"

    def __init__(self, token: str, base: str = "https://platform-api2.max.ru", ca_file: str = "", admin_chat_id=None):
        self.token = token
        self.base = base.rstrip("/")
        self.ssl = ssl.create_default_context()
        if ca_file:  # сертификат Минцифры для platform-api2.max.ru
            self.ssl.load_verify_locations(ca_file)
        self.groups = {admin_chat_id} if admin_chat_id else set()
        self.dialogs: dict = {}  # user_id -> chat_id диалога (нужно для «печатает…»)
        self.pending: dict = {}  # mid сообщения с кнопками -> callback_id последнего нажатия

    # ---------- HTTP ----------
    def request(self, method: str, path: str, params=None, body=None, timeout: float = 30):
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": self.token, "Content-Type": "application/json"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=timeout, context=self.ssl) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                err = e.read().decode(errors="replace")
                if e.code == 429 or (e.code == 400 and "not.ready" in err):
                    time.sleep(1 + attempt)
                    continue
                raise MaxError(f"{method} {path}: {e.code} {err[:300]}") from None
        raise MaxError(f"{method} {path}: слишком много попыток")

    def _target(self, chat_id):
        return {"chat_id": chat_id} if chat_id in self.groups else {"user_id": chat_id}

    # ---------- кнопки ----------
    @staticmethod
    def keyboard(markup):
        """Разметка в формате Telegram -> вложения MAX."""
        if not markup:
            return []
        rows = []
        if "inline_keyboard" in markup:
            for row in markup["inline_keyboard"]:
                rows.append([{"type": "link", "text": b["text"], "url": b["url"]} if "url" in b else
                             {"type": "callback", "text": b["text"], "payload": b["callback_data"]} for b in row])
        elif "keyboard" in markup:
            for row in markup["keyboard"]:
                rows.append([{"type": "request_contact", "text": b["text"]} if b.get("request_contact") else
                             {"type": "message", "text": b["text"]} for b in row])
        if not rows:
            return []
        return [{"type": "inline_keyboard", "payload": {"buttons": rows}}]

    # ---------- методы, которые использует handlers.py ----------
    def send(self, chat_id, text, markup=None, reply_to=None, attachments=None):
        body = {"text": text, "format": "html", "attachments": (attachments or []) + self.keyboard(markup)}
        if reply_to:
            body["link"] = {"type": "reply", "mid": reply_to}
        res = self.request("POST", "/messages", {**self._target(chat_id), "disable_link_preview": "true"}, body)
        return {"message_id": res["message"]["body"]["mid"]}

    def edit(self, chat_id, message_id, text, markup=None):
        body = {"text": text, "format": "html", "attachments": self.keyboard(markup)}
        cid = self.pending.pop(message_id, None)
        if cid:  # ответ на нажатие кнопки заодно меняет сообщение
            self.request("POST", "/answers", {"callback_id": cid}, {"message": body})
        else:
            self.request("PUT", "/messages", {"message_id": message_id}, body)

    def answer_callback(self, callback_id, text=None):
        if text:
            self.request("POST", "/answers", {"callback_id": callback_id}, {"notification": text})

    def typing(self, chat_id):
        dialog = self.dialogs.get(chat_id, chat_id)
        self.request("POST", f"/chats/{dialog}/actions", body={"action": "typing_on"})

    def forward(self, to_chat, from_chat, msg):
        res = self.request("POST", "/messages", self._target(to_chat),
                           {"link": {"type": "forward", "mid": msg["message_id"]}})
        return res["message"]["body"]["mid"]

    def copy(self, to_chat, msg):
        """Ответ администратора гостю: текст и картинки без имени администратора."""
        images = [{"type": "image", "payload": {"token": a["payload"]["token"]}}
                  for a in msg.get("_attachments", []) if a.get("type") == "image" and a.get("payload", {}).get("token")]
        text = msg.get("text") or ""
        if not text and not images:
            self.forward(to_chat, None, msg)
            return
        self.send(to_chat, html.escape(text), attachments=images)

    def ack(self, msg):
        self.send(msg["chat"]["id"], "✅ Отправлено гостю", reply_to=msg["message_id"])

    @staticmethod
    def user_ref(u: dict) -> str:
        name = html.escape(" ".join(filter(None, [u.get("first_name"), u.get("last_name")])) or "Гость")
        s = f'<a href="max://user/{u["id"]}">{name}</a>'
        if u.get("username"):
            s += f" (@{u['username']})"
        return s

    def upload_image(self, path: Path) -> str:
        url = self.request("POST", "/uploads", {"type": "image"})["url"]
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"data\"; filename=\"{path.name}\"\r\n"
                f"Content-Type: image/jpeg\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": self.token, "Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=120, context=self.ssl) as resp:
            res = json.load(resp)
        return next(iter(res["photos"].values()))["token"]

    def send_photos(self, chat_id, photos):
        """photos: токены или пути к файлам. Возвращает токены (их можно отправлять повторно)."""
        tokens = [self.upload_image(p) if isinstance(p, Path) else p for p in photos[:12]]
        self.request("POST", "/messages", self._target(chat_id),
                     {"attachments": [{"type": "image", "payload": {"token": t}} for t in tokens]})
        return tokens

    def set_commands(self, commands):
        self.request("PATCH", "/me/commands", body={
            "commands": [{"name": c["command"], "description": c["description"]} for c in commands]})

    # ---------- обновления ----------
    def get_updates(self, marker=None, timeout=30):
        res = self.request("GET", "/updates", {
            "timeout": timeout, "marker": marker,
            "types": "message_created,message_callback,bot_started,bot_added"}, timeout=timeout + 15)
        return res.get("updates", []), res.get("marker", marker)

    @staticmethod
    def _user(u: dict) -> dict:
        return {"id": u["user_id"], "first_name": u.get("first_name") or "", "last_name": u.get("last_name"),
                "username": u.get("username")}

    def _chat(self, recipient: dict, user_id):
        if recipient.get("chat_type") == "dialog":
            if recipient.get("chat_id"):
                self.dialogs[user_id] = recipient["chat_id"]
            return {"id": user_id, "type": "private"}
        self.groups.add(recipient["chat_id"])
        return {"id": recipient["chat_id"], "type": "group"}

    def convert(self, upd: dict):
        """Обновление MAX -> обновление в формате Telegram (или None, если его не нужно обрабатывать)."""
        kind = upd.get("update_type")
        if kind == "bot_started":
            user = self._user(upd["user"])
            self.dialogs[user["id"]] = upd["chat_id"]
            return {"message": {"message_id": None, "chat": {"id": user["id"], "type": "private"},
                                "from": user, "text": "/start"}}
        if kind == "bot_added":
            self.groups.add(upd["chat_id"])
            log.info("Бота добавили в чат %s", upd["chat_id"])
            try:
                self.send(upd["chat_id"], f"Здравствуйте! id этого чата: <code>{upd['chat_id']}</code>")
            except MaxError as e:
                log.warning("Не удалось написать в чат: %s", e)
            return None
        if kind == "message_callback":
            cb, m = upd["callback"], upd.get("message") or {}
            user = self._user(cb["user"])
            mid = (m.get("body") or {}).get("mid")
            if mid:
                if len(self.pending) > 1000:
                    self.pending.clear()
                self.pending[mid] = cb["callback_id"]
            chat = self._chat(m.get("recipient") or {"chat_type": "dialog"}, user["id"])
            return {"callback_query": {"id": cb["callback_id"], "data": cb.get("payload", ""), "from": user,
                                       "message": {"message_id": mid, "chat": chat} if mid else None}}
        if kind == "message_created":
            m = upd["message"]
            if not m.get("sender") or m["sender"].get("is_bot"):
                return None
            user = self._user(m["sender"])
            body = m.get("body") or {}
            msg = {"message_id": body.get("mid"), "chat": self._chat(m["recipient"], user["id"]), "from": user,
                   "text": body.get("text") or "", "_attachments": body.get("attachments") or []}
            for a in msg["_attachments"]:
                t = a.get("type")
                if t == "contact":
                    vcf = (a.get("payload") or {}).get("vcf_info") or ""
                    tel = re.search(r"TEL[^:\n]*:([+\d][\d\s()+-]*)", vcf)
                    msg["contact"] = {"phone_number": tel.group(1).strip() if tel else ""}
                elif t == "image":
                    msg["photo"] = [a]
                elif t == "file":
                    msg["document"] = a
            link = m.get("link") or {}
            if link.get("type") == "reply":
                msg["reply_to_message"] = {"message_id": (link.get("message") or {}).get("mid")}
            return {"message": msg}
        return None
