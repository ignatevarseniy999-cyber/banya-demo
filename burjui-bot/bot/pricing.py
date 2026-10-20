"""Расчёт стоимости. Цены не придумывает ИИ: всё считается здесь по data/ceny.json."""
import json
from dataclasses import dataclass, field
from datetime import date, timedelta

from .config import DATA_DIR

PRICES = json.loads((DATA_DIR / "ceny.json").read_text(encoding="utf-8"))
HOURLY = PRICES["hourly"]
DAILY = PRICES["daily"]

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]


class PriceError(ValueError):
    """Ошибка, которую можно показать гостю как есть."""


def money(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " ₽"


def day_label(d: date) -> str:
    return f"{DAY_SHORT[d.weekday()]}, {d.day} {MONTHS[d.month - 1]}"


def guests_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} гость"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} гостя"
    return f"{n} гостей"


def hours_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} час"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} часа"
    return f"{n} часов"


def nights_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} ночь"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return f"{n} ночи"
    return f"{n} ночей"


@dataclass
class Quote:
    room: str
    name: str
    total: int
    lines: list = field(default_factory=list)
    prepayment: int = 0
    deposit: int = 0

    def text(self) -> str:
        out = list(self.lines)
        out.append(f"💰 <b>Итого: {money(self.total)}</b>")
        if self.prepayment:
            out.append(f"Предоплата {money(self.prepayment)}, остаток {money(self.total - self.prepayment)} — на месте.")
        if self.deposit:
            out.append(f"Возвратный депозит {money(self.deposit)} (в сумму не входит).")
        return "\n".join(out)


def hourly_rate(room_id: str, day: date, guests: int) -> int:
    room = HOURLY["rooms"][room_id]
    if DAYS[day.weekday()] not in HOURLY["weekend_days"]:
        return room["weekday"]
    for tier in room["weekend"]:
        if guests <= tier["max_guests"]:
            return tier["price"]
    return room["weekend"][-1]["price"]


def quote_hourly(room_id: str, day: date, start_hour: int, hours: int, guests: int) -> Quote:
    """Цена берётся по дню начала брони, даже если бронь заходит за полночь."""
    if room_id not in HOURLY["rooms"]:
        raise PriceError("Такого зала нет.")
    room = HOURLY["rooms"][room_id]
    if not room["open"]:
        raise PriceError(f"«{room['name']}» {room['closed_note'][0].lower()}{room['closed_note'][1:]}.")
    if hours < HOURLY["min_hours"]:
        raise PriceError(f"Минимальное время аренды — {hours_word(HOURLY['min_hours'])}.")
    if guests < 1:
        raise PriceError("Укажите количество гостей.")
    if guests > room["max_guests"]:
        raise PriceError(f"«{room['name']}» вмещает до {guests_word(room['max_guests'])} (включая детей).")
    rate = hourly_rate(room_id, day, guests)
    end = (start_hour + hours) % 24
    lines = [
        f"🛁 <b>{room['name']}</b>",
        f"📅 {day_label(day)}, {start_hour:02d}:00–{end:02d}:00 ({hours_word(hours)})",
        f"👥 {guests_word(guests)}",
        f"{money(rate)} × {hours_word(hours)}",
    ]
    if room.get("bbq_zone"):
        lines.append(f"Мангальная зона по желанию — {money(room['bbq_zone'])} за весь период.")
    return Quote(room_id, room["name"], rate * hours, lines, prepayment=HOURLY["prepayment"])


def quote_daily(room_id: str, check_in: date, nights: int, guests: int) -> Quote:
    if room_id not in DAILY["rooms"]:
        raise PriceError("Такого варианта нет.")
    room = DAILY["rooms"][room_id]
    if nights < 1:
        raise PriceError("Минимум одна ночь.")
    if guests < 1:
        raise PriceError("Укажите количество гостей.")
    if guests > room["max_guests"]:
        raise PriceError(f"«{room['name']}» — максимум {guests_word(room['max_guests'])}.")
    extra = max(0, guests - room["included_guests"]) * room["extra_guest"]
    total = 0
    for i in range(nights):
        night = check_in + timedelta(days=i)
        base = room["weekend"] if DAYS[night.weekday()] in room["weekend_nights"] else room["weekday"]
        total += base + extra
    check_out = check_in + timedelta(days=nights)
    lines = [
        f"🏠 <b>{room['name']}</b>",
        f"📅 Заезд {day_label(check_in)} с {room['check_in']}, выезд {day_label(check_out)} до {room['check_out']}",
        f"🌙 {nights_word(nights)}, 👥 {guests_word(guests)}",
    ]
    if extra:
        lines.append(f"В цену входит {guests_word(room['included_guests'])}, доплата {money(room['extra_guest'])} за каждого следующего в сутки.")
    return Quote(room_id, room["name"], total, lines, deposit=room["deposit"])
