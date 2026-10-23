"""Тесты: python -m unittest discover tests"""
import tempfile
import unittest
from pathlib import Path

from carmon.app import App, parse_request, with_date_sort
from carmon.config import Config
from carmon.db import Database
from carmon.fetcher import FetchError
from carmon.parsers import parse

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class ParserTests(unittest.TestCase):
    def test_avito(self):
        items = parse("avito", fixture("avito.html"), "https://www.avito.ru/moskva/avtomobili")
        self.assertEqual(len(items), 3)
        first = items[0]
        self.assertEqual(first.id, "8482761404")
        self.assertEqual(first.title, "Toyota Camry 2.5 CVT, 2024, 584 км")
        self.assertEqual(first.price, 4250000)
        self.assertEqual(
            first.url,
            "https://www.avito.ru/moskva/avtomobili/toyota_camry_2.5_cvt_2024_584_km_8482761404",
        )
        self.assertEqual(first.location, "Москва, Генерала Тюленева")
        self.assertEqual(first.date, "1 час назад")
        self.assertTrue(first.image.startswith("https://"))

    def test_autoru(self):
        items = parse("autoru", fixture("autoru.html"), "https://auto.ru/moskva/cars/all/")
        self.assertEqual(len(items), 3)
        first = items[0]
        self.assertEqual(first.id, "1160035085-5884a8a0")
        self.assertEqual(first.price, 3949000)
        self.assertIn("Toyota Camry", first.title)
        self.assertEqual(first.location, "Москва, Каширское шоссе, 61к3А")
        self.assertTrue(first.image.startswith("https://"))

    def test_drom(self):
        items = parse("drom", fixture("drom.html"), "https://auto.drom.ru/toyota/camry/")
        self.assertEqual(len(items), 3)
        first = items[0]
        self.assertEqual(first.id, "165174438")
        self.assertEqual(first.price, 4350000)
        self.assertEqual(first.location, "Москва")
        self.assertEqual(first.url, "https://auto.drom.ru/moscow/toyota/camry/165174438.html")

    def test_fallback_to_links_when_markup_changes(self):
        page = '<a href="/moskva/avtomobili/lada_vesta_2020_123456789?x=1">Lada Vesta</a>'
        items = parse("avito", page, "https://www.avito.ru/moskva/avtomobili")
        self.assertEqual([(i.id, i.title) for i in items], [("123456789", "Lada Vesta")])


class RequestTests(unittest.TestCase):
    def test_text_goes_to_avito_with_prices(self):
        url, source, label, pmin, pmax = parse_request("камри 2018 от 1.2млн до 1 800 000")
        self.assertEqual(source, "avito")
        self.assertEqual(label, "камри 2018")
        self.assertEqual((pmin, pmax), (1_200_000, 1_800_000))
        self.assertIn("avito.ru/rossiya/avtomobili?", url)
        self.assertIn("s=104", url)
        self.assertIn("pmax=1800000", url)

    def test_link_with_price_filter(self):
        url, source, _, pmin, pmax = parse_request("https://auto.drom.ru/toyota/camry/ до 900к")
        self.assertEqual((url, source, pmin, pmax), ("https://auto.drom.ru/toyota/camry/", "drom", None, 900_000))

    def test_sort_added_once(self):
        self.assertEqual(
            with_date_sort("https://auto.ru/moskva/cars/all/?year_from=2015", "autoru"),
            "https://auto.ru/moskva/cars/all/?year_from=2015&sort=cr_date-desc",
        )
        self.assertEqual(
            with_date_sort("https://www.avito.ru/moskva/avtomobili?s=1", "avito"),
            "https://www.avito.ru/moskva/avtomobili?s=1",
        )

    def test_unknown_site(self):
        with self.assertRaises(ValueError):
            parse_request("https://example.com/cars")


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text, button_url=None):
        self.sent.append(text)

    def send_photo(self, chat_id, photo, caption, button_url=None):
        self.sent.append(caption)


class FakeFetcher:
    def __init__(self):
        self.pages = []

    def get(self, url, source):
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page

    def close(self):
        pass


def avito_card(item_id, price, title="Lada Vesta"):
    return (
        f'<div data-marker="item" data-item-id="{item_id}">'
        f'<a data-marker="item-title" href="/moskva/avtomobili/x_{item_id}">{title}</a>'
        f'<meta itemprop="price" content="{price}"></div>'
    )


class MonitorTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.db = Database(Path(tmp) / "test.db")
        self.tg = FakeTelegram()
        self.fetcher = FakeFetcher()
        config = Config("t", {1}, 60, "rossiya", Path(tmp) / "x.db", True, "", Path(tmp), "")
        self.app = App(config, self.db, self.fetcher, self.tg, self.tg)

    def check(self, *cards):
        self.fetcher.pages.append("".join(cards))
        self.app.check_search(self.db.searches()[0])

    def test_new_listings_and_price_drops(self):
        self.app.handle(1, "lada vesta до 1млн")
        self.tg.sent.clear()

        self.check(avito_card(1, 900000), avito_card(2, 800000))
        self.assertEqual(len(self.tg.sent), 1)
        self.assertIn("Поиск №1 запущен", self.tg.sent[0])

        self.check(avito_card(3, 700000), avito_card(4, 1500000), avito_card(1, 900000))
        self.assertEqual(len(self.tg.sent), 2)  # №4 дороже лимита
        self.assertIn("700 000 ₽", self.tg.sent[1])
        self.assertIn("/x_3", self.tg.sent[1])

        self.check(avito_card(3, 700000), avito_card(1, 850000))
        self.assertEqual(len(self.tg.sent), 3)
        self.assertIn("Цена снижена", self.tg.sent[2])

        self.check(avito_card(3, 700000), avito_card(1, 850000))
        self.assertEqual(len(self.tg.sent), 3)  # ничего нового

    def test_alert_after_repeated_failures_and_recovery(self):
        self.app.handle(1, "lada")
        self.check(avito_card(1, 1))
        self.tg.sent.clear()
        for _ in range(4):
            self.fetcher.pages.append(FetchError("капча"))
            self.app.check_search(self.db.searches()[0])
        self.assertEqual(len(self.tg.sent), 1)
        self.assertIn("3 раза подряд", self.tg.sent[0])
        self.check(avito_card(1, 1))
        self.assertIn("снова работает", self.tg.sent[-1])

    def test_commands(self):
        self.assertIn("Добавлен поиск №1", self.app.handle(1, "/add toyota"))
        self.assertIn("№1", self.app.handle(1, "/list"))
        self.assertIn("на паузе", self.app.handle(1, "/pause 1"))
        self.assertEqual(self.db.searches(active_only=True), [])
        self.assertIn("удалён", self.app.handle(1, "/del 1"))
        self.assertIn("личный бот", self.app.handle(999, "/list"))


if __name__ == "__main__":
    unittest.main()
