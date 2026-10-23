"""Telegram-бот с командами и цикл проверки поисков."""
from __future__ import annotations

import html
import logging
import random
import re
import threading
import time
from datetime import datetime
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

from .config import Config
from .db import Database, Search
from .fetcher import Fetcher, FetchError
from .parsers import SOURCE_NAMES, Listing, detect_source, parse
from .telegram import Telegram, TelegramError

log = logging.getLogger(__name__)

MAX_NOTIFY_PER_CHECK = 10  # защита от лавины, если сайт вдруг поменял вёрстку
FAILS_BEFORE_ALERT = 3

HELP = """<b>Мониторинг объявлений об авто</b>

Пришлите мне, что искать — и я буду присылать каждое новое объявление.

<b>Текстом</b> (ищу на Avito):
<code>toyota camry</code>
<code>камри 2018 от 1.2млн до 1.8млн</code>

<b>Ссылкой</b> на поиск Avito, Авто.ру или Drom — настройте на сайте любые фильтры (марка, год, пробег, город, цена) и скопируйте ссылку из адресной строки:
<code>https://auto.drom.ru/toyota/camry/</code>
К ссылке тоже можно дописать «от … до …».

<b>Команды</b>
/list — мои поиски
/del 3 — удалить поиск №3
/pause 3, /resume 3 — приостановить / возобновить
/check — проверить всё прямо сейчас
/help — эта справка"""

_PRICE_RE = re.compile(
    r"\b(от|до)\s*(\d[\d\s]*(?:[.,]\d+)?)\s*(млн|миллион\w*|м|тыс\w*|т|к)?(?=\s|$|₽|р)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://\S+")


def parse_amount(number: str, unit: str | None) -> int:
    value = float(number.replace(" ", "").replace(",", "."))
    unit = (unit or "").lower()
    if unit.startswith("м"):
        value *= 1_000_000
    elif unit.startswith(("т", "к")):
        value *= 1_000
    return int(value)


def parse_request(text: str, avito_region: str = "rossiya"):
    """Текст пользователя -> (url, source, label, price_min, price_max).

    Бросает ValueError с понятным сообщением, если разобрать не удалось.
    """
    price_min = price_max = None
    for word, number, unit in _PRICE_RE.findall(text):
        amount = parse_amount(number, unit)
        if word.lower() == "от":
            price_min = amount
        else:
            price_max = amount
    rest = _PRICE_RE.sub(" ", text)
    url_match = _URL_RE.search(rest)
    if url_match:
        url = url_match.group(0).rstrip(".,)")
        source = detect_source(url)
        if not source:
            raise ValueError("Пока умею следить только за Avito, Авто.ру и Drom.")
        url = with_date_sort(url, source)
        parts = urlsplit(url)
        label = (parts.netloc + parts.path).rstrip("/")
    else:
        query = " ".join(rest.split())
        if len(query) < 2:
            raise ValueError("Напишите, что искать, например: toyota camry до 1.5млн")
        params = {"q": query, "s": "104"}  # s=104 — сортировка «сначала новые»
        if price_min:
            params["pmin"] = price_min
        if price_max:
            params["pmax"] = price_max
        url = f"https://www.avito.ru/{avito_region}/avtomobili?" + urlencode(
            params, quote_via=quote_plus
        )
        source, label = "avito", query
    return url, source, label, price_min, price_max


def with_date_sort(url: str, source: str) -> str:
    """Добавляет к ссылке сортировку «сначала новые», если её там нет."""
    key, value = {"avito": ("s", "104"), "autoru": ("sort", "cr_date-desc")}.get(
        source, (None, None)
    )
    if not key:
        return url
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if any(k == key for k, _ in query):
        return url
    query.append((key, value))
    return urlunsplit(parts._replace(query=urlencode(query)))


def fmt_price(value: int | None) -> str:
    return f"{value:,}".replace(",", " ") + " ₽" if value else "цена не указана"


def price_ok(search: Search, price: int | None) -> bool:
    if price is None:
        return True
    if search.price_min and price < search.price_min:
        return False
    if search.price_max and price > search.price_max:
        return False
    return True


def format_listing(item: Listing, search: Search, old_price: int | None = None) -> str:
    e = html.escape
    if old_price:
        head = f"📉 <b>Цена снижена</b>: {fmt_price(old_price)} → <b>{fmt_price(item.price)}</b>"
    else:
        head = f"🆕 <b>{fmt_price(item.price)}</b>"
    lines = [head, f"<b>{e(item.title)}</b>"]
    if item.info:
        lines.append(e(item.info))
    if item.location:
        lines.append(f"📍 {e(item.location)}")
    if item.date:
        lines.append(f"🕒 {e(item.date)}")
    lines.append(f"🔎 {SOURCE_NAMES[item.source]} · поиск №{search.id} «{e(search.label)}»")
    lines.append(f'<a href="{e(item.url)}">{e(item.url)}</a>')
    return "\n".join(lines)


def describe(search: Search) -> str:
    e = html.escape
    status = "✅" if search.active else "⏸"
    prices = []
    if search.price_min:
        prices.append(f"от {fmt_price(search.price_min)}")
    if search.price_max:
        prices.append(f"до {fmt_price(search.price_max)}")
    line = f"{status} <b>№{search.id}</b> {SOURCE_NAMES[search.source]} — {e(search.label)}"
    if prices:
        line += " (" + " ".join(prices) + ")"
    if search.last_check:
        line += f"\n    проверено в {datetime.fromtimestamp(search.last_check):%H:%M:%S}"
    if search.last_error:
        line += f"\n    ⚠️ {e(search.last_error)}"
    return line


class App:
    def __init__(self, config: Config, db: Database, fetcher: Fetcher, tg: Telegram, bot_tg: Telegram):
        self.config = config
        self.db = db
        self.fetcher = fetcher
        self.tg = tg  # для уведомлений из цикла проверки
        self.bot_tg = bot_tg  # для ответов на команды (свой поток)
        self.wake = threading.Event()
        self.stop = threading.Event()

    # --- запуск ----------------------------------------------------------------
    def run(self) -> None:
        threading.Thread(target=self.poll_commands, name="telegram", daemon=True).start()
        log.info("Бот запущен. Напишите ему в Telegram /start")
        try:
            while not self.stop.is_set():
                self.check_all()
                delay = self.config.check_interval * random.uniform(0.8, 1.2)
                self.wake.wait(delay)
                self.wake.clear()
        except KeyboardInterrupt:
            log.info("Остановка…")
        finally:
            self.stop.set()
            self.fetcher.close()

    # --- проверка поисков -------------------------------------------------------
    def check_all(self) -> None:
        searches = self.db.searches(active_only=True)
        for i, search in enumerate(searches):
            if self.stop.is_set():
                return
            if i:
                time.sleep(random.uniform(2, 5))  # не долбим сайты запросами подряд
            self.check_search(search)

    def check_search(self, search: Search) -> None:
        try:
            page = self.fetcher.get(search.url, search.source)
            items = parse(search.source, page, search.url)
            if not items:
                raise FetchError("на странице не найдено объявлений")
        except FetchError as exc:
            fails = self.db.mark_checked(search.id, str(exc))
            log.warning("Поиск №%s: %s (ошибок подряд: %s)", search.id, exc, fails)
            if fails == FAILS_BEFORE_ALERT:
                self.notify(
                    search.chat_id,
                    f"⚠️ Поиск №{search.id} не проверяется уже {fails} раза подряд: "
                    f"{html.escape(str(exc))}.\n\nЕсли это капча — решите её в окне браузера "
                    "на компьютере, где запущен бот. Если в выдаче просто пусто — "
                    "ослабьте фильтры. Я продолжаю попытки.",
                )
            return

        seen = self.db.seen_prices(search.id)
        fresh, cheaper = [], []
        for item in items:
            if item.id not in seen:
                fresh.append(item)
            elif item.price and seen[item.id] and item.price < seen[item.id]:
                cheaper.append((item, seen[item.id]))
            self.db.remember(search.id, item.id, item.price)
        self.db.mark_checked(search.id, None)

        if not search.initialized:
            self.notify(
                search.chat_id,
                f"✅ Поиск №{search.id} запущен: сейчас на странице {len(items)} объявл. "
                "Буду присылать только новые — сразу, как появятся.",
            )
            return
        if search.fail_count >= FAILS_BEFORE_ALERT:
            self.notify(search.chat_id, f"✅ Поиск №{search.id} снова работает.")

        fresh = [i for i in fresh if price_ok(search, i.price)]
        cheaper = [(i, p) for i, p in cheaper if price_ok(search, i.price)]
        log.info(
            "Поиск №%s: %s объявл., новых %s, подешевели %s",
            search.id, len(items), len(fresh), len(cheaper),
        )
        for item in fresh[:MAX_NOTIFY_PER_CHECK]:
            self.send_listing(search, item)
        if len(fresh) > MAX_NOTIFY_PER_CHECK:
            self.notify(
                search.chat_id,
                f"…и ещё {len(fresh) - MAX_NOTIFY_PER_CHECK} новых по поиску №{search.id}: "
                f'<a href="{html.escape(search.url)}">открыть выдачу</a>',
            )
        for item, old_price in cheaper[:MAX_NOTIFY_PER_CHECK]:
            self.send_listing(search, item, old_price)

    def send_listing(self, search: Search, item: Listing, old_price: int | None = None) -> None:
        text = format_listing(item, search, old_price)
        try:
            if item.image:
                try:
                    self.tg.send_photo(search.chat_id, item.image, text, item.url)
                    return
                except TelegramError as exc:
                    log.info("Фото не отправилось (%s), шлю текстом", exc)
            self.tg.send_message(search.chat_id, text, item.url)
        except TelegramError as exc:
            log.error("Не удалось отправить уведомление: %s", exc)

    def notify(self, chat_id: int, text: str) -> None:
        try:
            self.tg.send_message(chat_id, text)
        except TelegramError as exc:
            log.error("Не удалось отправить сообщение: %s", exc)

    # --- команды бота -----------------------------------------------------------
    def poll_commands(self) -> None:
        offset = None
        while not self.stop.is_set():
            try:
                updates = self.bot_tg.get_updates(offset)
            except TelegramError as exc:
                if "Unauthorized" in str(exc) or "Not Found" in str(exc):
                    log.error("Telegram отклонил токен бота — проверьте TELEGRAM_BOT_TOKEN")
                    self.stop.set()
                    self.wake.set()
                    return
                log.warning("Telegram: %s", exc)
                time.sleep(5)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                message = update.get("message") or {}
                text = (message.get("text") or "").strip()
                chat_id = message.get("chat", {}).get("id")
                if not text or chat_id is None:
                    continue
                try:
                    reply = self.handle(chat_id, text)
                except Exception:
                    log.exception("Ошибка при обработке команды")
                    reply = "Что-то пошло не так, подробности в консоли бота."
                if reply:
                    try:
                        self.bot_tg.send_message(chat_id, reply)
                    except TelegramError as exc:
                        log.error("Не удалось ответить: %s", exc)

    def is_allowed(self, chat_id: int) -> bool:
        allowed = self.config.allowed_chat_ids | self.db.owners()
        if not allowed:
            # Первый, кто написал боту, становится владельцем
            self.db.add_owner(chat_id)
            log.info("Владелец бота: chat_id=%s", chat_id)
            return True
        return chat_id in allowed

    def handle(self, chat_id: int, text: str) -> str | None:
        if not self.is_allowed(chat_id):
            log.warning("Сообщение от чужого chat_id=%s проигнорировано", chat_id)
            return (
                f"Это личный бот. Ваш chat id: <code>{chat_id}</code> — владелец может "
                "добавить его в ALLOWED_CHAT_IDS."
            )
        command, _, arg = text.partition(" ")
        command = command.split("@")[0].lower()
        arg = arg.strip()

        if command in ("/start", "/help"):
            return HELP
        if command == "/list":
            searches = self.db.searches(chat_id)
            if not searches:
                return "Поисков пока нет. Пришлите текст или ссылку — добавлю."
            return "\n\n".join(describe(s) for s in searches)
        if command == "/check":
            self.wake.set()
            return "🔄 Проверяю все поиски…"
        if command in ("/del", "/delete", "/pause", "/resume"):
            search = self._own_search(chat_id, arg)
            if isinstance(search, str):
                return search
            if command in ("/del", "/delete"):
                self.db.delete_search(search.id)
                return f"🗑 Поиск №{search.id} удалён."
            self.db.set_active(search.id, command == "/resume")
            if command == "/resume":
                self.wake.set()
                return f"▶️ Поиск №{search.id} снова активен."
            return f"⏸ Поиск №{search.id} на паузе."
        if command == "/add":
            text = arg
        elif command.startswith("/"):
            return "Не знаю такой команды. /help — справка."

        try:
            url, source, label, pmin, pmax = parse_request(text, self.config.avito_region)
        except ValueError as exc:
            return str(exc)
        search_id = self.db.add_search(chat_id, url, source, label, pmin, pmax)
        self.wake.set()
        return (
            f"➕ Добавлен поиск №{search_id}: {SOURCE_NAMES[source]} — {html.escape(label)}\n"
            f'<a href="{html.escape(url)}">ссылка на выдачу</a>\n'
            "Сейчас запомню текущие объявления и дальше буду присылать только новые."
        )

    def _own_search(self, chat_id: int, arg: str) -> Search | str:
        if not arg.lstrip("№#").isdigit():
            return "Укажите номер поиска, например: /del 2 (номера — в /list)"
        search = self.db.get_search(int(arg.lstrip("№#")))
        if not search or search.chat_id != chat_id:
            return "Нет такого поиска. Посмотрите номера в /list"
        return search
