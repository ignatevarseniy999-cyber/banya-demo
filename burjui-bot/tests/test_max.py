"""Бот в MAX без сети: обновления MAX -> логика бота -> запросы к API MAX."""
import itertools
import unittest
from datetime import date
from unittest import mock

from bot import config, handlers
from bot.max_api import Max
from bot.storage import Storage

ADMIN = -7000
USER = {"user_id": 42, "first_name": "Ирина", "last_name": "Петрова", "username": None, "is_bot": False}


class FakeMax(Max):
    def __init__(self):
        super().__init__("token", admin_chat_id=ADMIN)
        self.log = []
        self.ids = itertools.count(1)

    def request(self, method, path, params=None, body=None, timeout=30):
        self.log.append((method, path, params or {}, body))
        if method == "POST" and path == "/messages":
            return {"message": {"body": {"mid": f"mid.{next(self.ids)}"}}}
        return {"success": True}

    def sent(self, **target):
        return [b for m, p, prm, b in self.log if m == "POST" and p == "/messages"
                and all(prm.get(k) == v for k, v in target.items())]


def dialog_msg(text="", mid="mid.u1", attachments=None, link=None):
    return {"update_type": "message_created", "timestamp": 1, "message": {
        "sender": USER, "recipient": {"chat_id": 900, "chat_type": "dialog"},
        "body": {"mid": mid, "seq": 1, "text": text, "attachments": attachments}, "link": link}}


def callback(payload, mid):
    return {"update_type": "message_callback", "timestamp": 1,
            "callback": {"timestamp": 1, "callback_id": f"cb-{payload}", "payload": payload, "user": USER},
            "message": {"recipient": {"chat_id": 900, "chat_type": "dialog"}, "body": {"mid": mid, "seq": 2}}}


class MaxFlow(unittest.TestCase):
    def setUp(self):
        self.mx = FakeMax()
        self.bot = handlers.Bot(self.mx, Storage(":memory:"))
        for p in (mock.patch.object(config, "ADMIN_CHAT_ID", ADMIN),
                  mock.patch.object(handlers, "now_msk", return_value=mock.Mock(date=lambda: date(2026, 10, 20)))):
            p.start()
            self.addCleanup(p.stop)

    def feed(self, upd):
        conv = self.mx.convert(upd)
        if conv:
            self.bot.handle(conv)

    def test_start_menu_has_callback_buttons(self):
        self.feed({"update_type": "bot_started", "timestamp": 1, "chat_id": 900, "user": USER})
        body = self.mx.sent(user_id=42)[-1]
        self.assertEqual(body["format"], "html")
        kb = body["attachments"][0]
        self.assertEqual(kb["type"], "inline_keyboard")
        self.assertIn({"type": "callback", "text": "📅 Рассчитать цену и забронировать", "payload": "bk"},
                      [b for row in kb["payload"]["buttons"] for b in row])

    def test_booking_edits_via_callback_answer_and_reaches_admins(self):
        for data in ["bk", "b:baron", "dt:20261024", "t:18", "hr:3", "g:5"]:
            self.feed(callback(data, "mid.menu"))
        answers = [(prm, b) for m, p, prm, b in self.mx.log if p == "/answers"]
        self.assertEqual(answers[-1][0]["callback_id"], "cb-g:5")
        self.assertIn("Итого: 14 100 ₽", answers[-1][1]["message"]["text"])
        self.feed(callback("ok", "mid.menu"))
        ask = self.mx.sent(user_id=42)[-1]
        self.assertEqual(ask["attachments"][0]["payload"]["buttons"][0][0]["type"], "request_contact")
        vcf = "BEGIN:VCARD\nVERSION:3.0\nTEL;TYPE=cell:79001234567\nEND:VCARD"
        self.feed(dialog_msg(attachments=[{"type": "contact", "payload": {"vcf_info": vcf}}]))
        admin = self.mx.sent(chat_id=ADMIN)[-1]["text"]
        self.assertIn("Заявка №1", admin)
        self.assertIn("+79001234567", admin)
        self.assertIn("max://user/42", admin)

    def test_admin_reply_is_copied_to_guest(self):
        self.feed(callback("a", "mid.menu"))
        self.feed(dialog_msg("Можно с ребёнком 3 лет?"))
        admin_mid = self.bot.db.db.execute("SELECT admin_message_id FROM relay").fetchone()[0]
        self.feed({"update_type": "message_created", "timestamp": 2, "message": {
            "sender": {"user_id": 7, "first_name": "Админ", "is_bot": False},
            "recipient": {"chat_id": ADMIN, "chat_type": "chat"},
            "body": {"mid": "mid.a1", "seq": 3, "text": "Да, можно"},
            "link": {"type": "reply", "message": {"mid": admin_mid, "seq": 1}}}})
        self.assertEqual(self.mx.sent(user_id=42)[-1]["text"], "Да, можно")
        self.assertEqual(self.mx.sent(chat_id=ADMIN)[-1]["text"], "✅ Отправлено гостю")

    def test_receipt_image_forwarded(self):
        self.feed(dialog_msg(attachments=[{"type": "image", "payload": {"token": "t1"}}]))
        self.assertIn("чек получили", self.mx.sent(user_id=42)[-1]["text"])
        fwd = self.mx.sent(chat_id=ADMIN)[-1]
        self.assertEqual(fwd["link"], {"type": "forward", "mid": "mid.u1"})

    def test_bot_added_reports_chat_id(self):
        self.feed({"update_type": "bot_added", "timestamp": 1, "chat_id": -555, "user": USER})
        self.assertIn("-555", self.mx.sent(chat_id=-555)[-1]["text"])

    def test_link_buttons(self):
        kb = Max.keyboard({"inline_keyboard": [[{"text": "Сайт", "url": "https://burjuiclub.ru"}]]})
        self.assertEqual(kb[0]["payload"]["buttons"], [[{"type": "link", "text": "Сайт", "url": "https://burjuiclub.ru"}]])


if __name__ == "__main__":
    unittest.main()
