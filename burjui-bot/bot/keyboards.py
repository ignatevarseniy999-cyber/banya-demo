"""Кнопки."""
from datetime import date, timedelta

from . import texts
from .pricing import DAILY, DAY_SHORT, HOURLY


def ikb(rows):
    """rows: список рядов, кнопка — (текст, callback) или (текст, None, url)."""
    out = []
    for row in rows:
        r = []
        for b in row:
            if len(b) == 3:
                r.append({"text": b[0], "url": b[2]})
            else:
                r.append({"text": b[0], "callback_data": b[1]})
        out.append(r)
    return {"inline_keyboard": out}


def chunks(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


def main_menu():
    return ikb([
        [("🛁 Сауны на часы", "h"), ("🏠 Жильё на сутки", "d")],
        [("📅 Рассчитать цену и забронировать", "bk")],
        [("❓ Частые вопросы", "f"), ("📍 Адрес и контакты", "c")],
        [("🌲 Коттеджи «Провинция»", "p")],
        [("👤 Написать администратору", "a")],
    ])


def back_menu():
    return ikb([[("↩ В меню", "m")]])


def hourly_list(prefix="r"):
    rows = chunks([(name, f"{prefix}:{rid}") for rid, name in texts.hourly_rooms()], 2)
    return ikb(rows + [[("↩ В меню", "m")]])


def daily_list(prefix="r"):
    rows = [[(name, f"{prefix}:{rid}") for rid, name in texts.daily_rooms()]]
    return ikb(rows + [[("↩ В меню", "m")]])


def booking_choice():
    rows = chunks([(name, f"b:{rid}") for rid, name in texts.hourly_rooms() if "ремонт" not in name], 3)
    rows.append([(name, f"b:{rid}") for rid, name in texts.daily_rooms()])
    return ikb(rows + [[("↩ В меню", "m")]])


def room_card(room_id: str, has_photos: bool, site_url: str | None):
    rows = []
    if has_photos:
        rows.append([("📷 Фото", f"ph:{room_id}")])
    elif site_url:
        rows.append([("📷 Фото на сайте", None, site_url)])
    if room_id in HOURLY["rooms"] and not HOURLY["rooms"][room_id]["open"]:
        rows.append([("🛁 Посмотреть Герцог", "r:gercog")])
        rows.append([("👤 Узнать об открытии", "a")])
        back = "h"
    else:
        rows.append([("📅 Рассчитать цену и забронировать", f"b:{room_id}")])
        back = "h" if room_id in HOURLY["rooms"] else "d"
    rows.append([("← Назад", back), ("↩ Меню", "m")])
    return ikb(rows)


def dates(today: date, days: int = 14, back: str = "m"):
    btns = []
    for i in range(days):
        d = today + timedelta(days=i)
        label = "Сегодня" if i == 0 else "Завтра" if i == 1 else f"{DAY_SHORT[d.weekday()]} {d:%d.%m}"
        btns.append((label, f"dt:{d:%Y%m%d}"))
    rows = chunks(btns, 3)
    rows.append([("🗓 Другая дата", "dto")])
    rows.append([("← Назад", back), ("✖ Отмена", "m")])
    return ikb(rows)


def start_times():
    rows = chunks([(f"{h:02d}:00", f"t:{h}") for h in range(24)], 6)
    rows.append([("← Назад", "back:date"), ("✖ Отмена", "m")])
    return ikb(rows)


def durations():
    rows = [[(f"{h} ч", f"hr:{h}") for h in (2, 3, 4)], [(f"{h} ч", f"hr:{h}") for h in (5, 6, 8)]]
    rows.append([("Другое", "hro")])
    rows.append([("← Назад", "back:time"), ("✖ Отмена", "m")])
    return ikb(rows)


def nights():
    rows = chunks([(str(n), f"n:{n}") for n in range(1, 8)], 4)
    rows.append([("← Назад", "back:date"), ("✖ Отмена", "m")])
    return ikb(rows)


def guests(max_guests: int, back: str):
    rows = chunks([(str(n), f"g:{n}") for n in range(1, max_guests + 1)], 5)
    rows.append([("← Назад", back), ("✖ Отмена", "m")])
    return ikb(rows)


def confirm_hourly():
    return ikb([
        [("✅ Всё верно, оставить заявку", "ok")],
        [("✏️ Изменить", "back:guests"), ("✖ Отмена", "m")],
    ])


def confirm_daily():
    return ikb([
        [("🔗 Забронировать и оплатить онлайн", None, DAILY["booking_link"])],
        [("📝 Оставить заявку администратору", "ok")],
        [("✏️ Изменить", "back:guests"), ("↩ Меню", "m")],
    ])


def phone_request():
    return {
        "keyboard": [[{"text": "📱 Отправить мой номер", "request_contact": True}], [{"text": "✖ Отмена"}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }


def remove_reply():
    return {"remove_keyboard": True}


def faq_menu():
    rows = chunks([(title, f"f:{key}") for key, (title, _) in texts.FAQ.items()], 2)
    return ikb(rows + [[("↩ В меню", "m")]])


def faq_section():
    return ikb([[("← Все вопросы", "f"), ("↩ Меню", "m")], [("👤 Спросить администратора", "a")]])


def contacts():
    return ikb([[("🗺 Открыть на карте", None, texts.MAP_URL)], [("↩ В меню", "m")]])


def provinciya():
    rows = [[(name, None, url) for name, url in texts.PROVINCIYA_ADMIN.items()]]
    rows.append([("🌐 Сайт Провинции", None, texts.PROVINCIYA_SITE)])
    rows.append([("↩ В меню", "m")])
    return ikb(rows)


def after_lead():
    return ikb([[("↩ В меню", "m")]])


def fallback():
    return ikb([[("📋 Меню", "m"), ("👤 Администратор", "a")]])
