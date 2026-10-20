"""Проверка цен и сценариев без Telegram: python -m unittest -v"""
import os
import tempfile
import unittest
from datetime import date
from unittest import mock

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "test.sqlite3"))

from bot import config, handlers  # noqa: E402
from bot.pricing import PriceError, quote_daily, quote_hourly  # noqa: E402
from bot.storage import Storage  # noqa: E402

SAT = date(2026, 10, 24)
TUE = date(2026, 10, 20)
FRI = date(2026, 10, 23)
SUN = date(2026, 10, 25)


class Prices(unittest.TestCase):
    def test_baron_saturday_5_guests(self):
        self.assertEqual(quote_hourly("baron", SAT, 18, 3, 5).total, 14100)

    def test_baron_saturday_4_guests(self):
        self.assertEqual(quote_hourly("baron", SAT, 18, 2, 4).total, 9000)

    def test_markiz_tuesday(self):
        self.assertEqual(quote_hourly("markiz", TUE, 12, 2, 2).total, 6600)

    def test_gercog_sunday_tiers(self):
        self.assertEqual(quote_hourly("gercog", SUN, 18, 4, 16).total, 20000)
        self.assertEqual(quote_hourly("gercog", SUN, 18, 2, 15).total, 9000)
        self.assertEqual(quote_hourly("gercog", SUN, 18, 2, 11).total, 8400)
        self.assertEqual(quote_hourly("gercog", SUN, 18, 2, 6).total, 8000)
        self.assertEqual(quote_hourly("gercog", TUE, 18, 2, 20).total, 7400)

    def test_friday_is_weekend_for_hourly(self):
        self.assertEqual(quote_hourly("markiz", FRI, 20, 2, 5).total, 8000)

    def test_prepayment(self):
        q = quote_hourly("baron", SAT, 18, 3, 5)
        self.assertEqual(q.prepayment, 2000)
        self.assertIn("остаток 12 100 ₽", q.text())

    def test_limits(self):
        with self.assertRaises(PriceError):
            quote_hourly("baron", SAT, 18, 1, 2)
        with self.assertRaises(PriceError):
            quote_hourly("baron", SAT, 18, 2, 11)
        with self.assertRaises(PriceError):
            quote_hourly("monarh", SAT, 18, 2, 2)

    def test_vikont_weekend(self):
        q = quote_daily("vikont", FRI, 2, 6)  # ночи пт и сб: (20 000 + 2 000) × 2
        self.assertEqual(q.total, 44000)
        self.assertEqual(q.deposit, 10000)

    def test_vikont_sunday_is_weekday(self):
        self.assertEqual(quote_daily("vikont", SUN, 1, 4).total, 16000)

    def test_studio(self):
        self.assertEqual(quote_daily("studio", TUE, 3, 3).total, 15000)
        with self.assertRaises(PriceError):
            quote_daily("studio", TUE, 1, 4)


class FakeTG:
    def __init__(self):
        self.calls = []
        self.mid = 100

    def call(self, method, _timeout=30, **params):
        self.calls.append((method, params))
        self.mid += 1
        return {"message_id": self.mid}

    def send(self, chat_id, text, markup=None, **kw):
        return self.call("sendMessage", chat_id=chat_id, text=text, reply_markup=markup)

    def edit(self, chat_id, message_id, text, markup=None):
        return self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup)

    def answer_callback(self, *a, **k):
        pass

    def send_photos(self, chat_id, photos):
        self.call("sendMediaGroup", chat_id=chat_id, media=photos)
        return [f"id{i}" for i in range(len(photos))]

    def texts(self, chat_id=None):
        return [p["text"] for m, p in self.calls
                if m in ("sendMessage", "editMessageText") and (chat_id is None or p["chat_id"] == chat_id)]

    def buttons(self):
        for m, p in reversed(self.calls):
            mk = p.get("reply_markup") or {}
            if "inline_keyboard" in mk:
                return [b.get("callback_data") for row in mk["inline_keyboard"] for b in row]
        return []


USER = {"id": 555, "first_name": "Анна", "username": "anna"}
ADMIN = -1001


def msg(text=None, **extra):
    m = {"message_id": 1, "chat": {"id": USER["id"], "type": "private"}, "from": USER}
    if text is not None:
        m["text"] = text
    m.update(extra)
    return {"message": m}


def cb(data):
    return {"callback_query": {"id": "1", "data": data, "from": USER,
                               "message": {"message_id": 50, "chat": {"id": USER["id"], "type": "private"}}}}


class Flow(unittest.TestCase):
    def setUp(self):
        self.tg = FakeTG()
        self.bot = handlers.Bot(self.tg, Storage(":memory:"))
        p = mock.patch.object(config, "ADMIN_CHAT_ID", ADMIN)
        p.start()
        self.addCleanup(p.stop)
        now = mock.patch.object(handlers, "now_msk", return_value=mock.Mock(date=lambda: TUE))
        now.start()
        self.addCleanup(now.stop)

    def test_start_shows_menu(self):
        self.bot.handle(msg("/start"))
        self.assertIn("Буржуй клаба", self.tg.texts()[-1])
        self.assertIn("bk", self.tg.buttons())

    def test_hourly_booking_end_to_end(self):
        for data in ["bk", "b:baron", "dt:20261024", "t:18", "hr:3", "g:5"]:
            self.bot.handle(cb(data))
        summary = self.tg.texts()[-1]
        self.assertIn("Итого: 14 100 ₽", summary)
        self.assertIn("ok", self.tg.buttons())
        self.bot.handle(cb("ok"))
        self.bot.handle(msg(contact={"phone_number": "79001234567", "user_id": USER["id"]}))
        admin = self.tg.texts(ADMIN)[-1]
        self.assertIn("Заявка №1", admin)
        self.assertIn("Барон", admin)
        self.assertIn("+79001234567", admin)
        self.assertIn("14 100 ₽", admin)
        self.assertIn("подтвердит бронь", self.tg.texts(USER["id"])[-1])

    def test_admin_reply_goes_to_guest(self):
        self.bot.handle(cb("a"))
        self.bot.handle(msg("Можно продлить до утра?"))
        admin_mid = self.bot.db.db.execute("SELECT admin_message_id FROM relay").fetchone()[0]
        self.bot.handle({"message": {"message_id": 9, "chat": {"id": ADMIN, "type": "supergroup"},
                                     "from": {"id": 1}, "text": "Да, можно",
                                     "reply_to_message": {"message_id": admin_mid}}})
        copy = [p for m, p in self.tg.calls if m == "copyMessage"]
        self.assertEqual(copy[-1]["chat_id"], USER["id"])

    def test_typed_phone_and_date(self):
        for data in ["b:markiz", "dto"]:
            self.bot.handle(cb(data))
        self.bot.handle(msg("23.10"))
        self.assertIn("пт, 23 октября", self.tg.texts()[-1])
        for data in ["t:20", "hr:2", "g:5", "ok"]:
            self.bot.handle(cb(data))
        self.bot.handle(msg("8 (900) 123-45-67"))
        self.assertIn("+79001234567", self.tg.texts(ADMIN)[-1])
        self.assertIn("8 000 ₽", self.tg.texts(ADMIN)[-1])

    def test_daily_offers_online_link(self):
        for data in ["b:vikont", "dt:20261023", "n:2", "g:6"]:
            self.bot.handle(cb(data))
        self.assertIn("Итого: 44 000 ₽", self.tg.texts()[-1])
        mk = self.tg.calls[-1][1]["reply_markup"]["inline_keyboard"]
        self.assertTrue(any(b.get("url", "").startswith("https://homereserve.ru") for row in mk for b in row))

    def test_monarh_cannot_be_booked(self):
        self.bot.handle(cb("b:monarh"))
        self.assertIn("ремонт", self.tg.texts()[-1])
        self.assertNotIn("dt:", " ".join(filter(None, self.tg.buttons())))

    def test_receipt_forwarded(self):
        self.bot.handle(msg(photo=[{"file_id": "x"}]))
        self.assertIn("чек получили", self.tg.texts(USER["id"])[-1])
        self.assertTrue(any(m == "forwardMessage" for m, _ in self.tg.calls))

    def test_keywords_without_ai(self):
        self.bot.handle(msg("а можно с собакой?"))
        self.assertIn("Правила", self.tg.texts()[-1])
        self.bot.handle(msg("есть массаж?"))
        self.assertIn("Массажа", self.tg.texts()[-1])
        self.bot.handle(msg("хочу коттедж"))
        self.assertIn("Провинция", self.tg.texts()[-1])
        self.bot.handle(msg("сколько стоит в субботу?"))
        self.assertIn("Что бронируем", self.tg.texts()[-1])


if __name__ == "__main__":
    unittest.main()
