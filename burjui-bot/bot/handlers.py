"""Логика бота: меню, бронирование по шагам, частые вопросы, связь с администраторами."""
import html
import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, texts
from . import keyboards as kb
from .pricing import DAILY, HOURLY, PriceError, day_label, quote_daily, quote_hourly
from .channel import ChannelError

log = logging.getLogger(__name__)
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".webp"}

KEYWORDS = [
    (("барон",), ("room", "baron")),
    (("маркиз",), ("room", "markiz")),
    (("герцог",), ("room", "gercog")),
    (("монарх",), ("room", "monarh")),
    (("виконт",), ("room", "vikont")),
    (("студи",), ("room", "studio")),
    (("коттедж", "провинц", "загород"), ("prov", None)),
    (("массаж", "банщик", "девуш", "девочк"), ("faq", "other")),
    (("бассейн", "плава", "хлор", "нарукавник", "круг"), ("faq", "pool")),
    (("джакузи",), ("faq", "jacuzzi")),
    (("парн", "хаммам", "хамам", "сауна", "температур", "веник", "пар "), ("faq", "steam")),
    (("предоплат", "отмен", "перенос", "вернут", "возврат", "оплат", "чек"), ("faq", "pay")),
    (("живот", "собак", "кошк", "дет", "кури", "алкогол", "свою ед", "своё", "свое", "музык", "правил"), ("faq", "rules")),
    (("полотен", "халат", "тапоч", "уголь", "шашлык", "мангал", "кальян", "караоке", "колонк", "пицц", "простын"), ("faq", "extras")),
    (("адрес", "где вы", "как добраться", "доехать", "парков", "телефон", "контакт", "находит"), ("contacts", None)),
    (("администратор", "админ", "человек", "оператор", "позвон", "менеджер"), ("admin", None)),
    (("цен", "стоим", "сколько", "брон", "свобод", "записат", "прайс"), ("book", None)),
    (("фото", "посмотреть"), ("halls", None)),
]


def now_msk() -> datetime:
    return datetime.now(ZoneInfo(config.TIMEZONE))


def parse_date(text: str, today: date):
    m = re.search(r"(\d{1,2})[.\-/ ](\d{1,2})(?:[.\-/ ](\d{2,4}))?", text)
    if not m:
        return None
    d, mth, y = int(m.group(1)), int(m.group(2)), m.group(3)
    year = (2000 + int(y) if len(y) == 2 else int(y)) if y else today.year
    try:
        res = date(year, mth, d)
    except ValueError:
        return None
    if not y and res < today:
        res = date(year + 1, mth, d)
    if res < today or res > today + timedelta(days=366):
        return None
    return res


def parse_phone(text: str):
    digits = re.sub(r"\D", "", text)
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        return "+7" + digits
    return None


class Bot:
    def __init__(self, tg, storage, assistant=None):
        self.tg = tg
        self.db = storage
        self.ai = assistant
        self.state: dict[int, dict] = {}

    # ---------- вход ----------
    def handle(self, upd: dict) -> None:
        try:
            if "callback_query" in upd:
                self.on_callback(upd["callback_query"])
            elif "message" in upd:
                self.on_message(upd["message"])
        except Exception:
            log.exception("Ошибка при обработке обновления %s", upd.get("update_id"))

    def on_message(self, msg: dict) -> None:
        chat = msg["chat"]
        text = (msg.get("text") or "").strip()
        if text.startswith("/id"):
            self.tg.send(chat["id"], f"id этого чата: <code>{chat['id']}</code>")
            return
        if config.ADMIN_CHAT_ID and chat["id"] == config.ADMIN_CHAT_ID:
            self.on_admin_message(msg)
            return
        if chat["type"] != "private":
            return
        uid = chat["id"]
        st = self.state.setdefault(uid, {})
        cmd = text.split("@")[0].lower() if text.startswith("/") else ""

        if cmd in ("/start", "/menu"):
            self.state[uid] = {}
            if self.ai:
                self.ai.reset(uid)
            self.tg.send(uid, texts.GREETING if cmd == "/start" else texts.MENU_TITLE, kb.main_menu())
            return
        if cmd == "/halls":
            self.tg.send(uid, texts.HOURLY_INTRO, kb.hourly_list())
            return
        if cmd == "/book":
            self.tg.send(uid, "Что бронируем?", kb.booking_choice())
            return
        if cmd == "/admin":
            st["await"] = "handoff"
            self.tg.send(uid, texts.HANDOFF_ASK)
            return

        if "contact" in msg and st.get("await") == "phone":
            self.finish_lead(msg, msg["contact"].get("phone_number", ""))
            return
        if msg.get("photo") or msg.get("document"):
            self.on_receipt(msg)
            return
        if not text:
            return

        wait = st.get("await")
        if wait == "phone":
            if text == "✖ Отмена":
                st.clear()
                self.tg.send(uid, "Заявку отменили.", kb.remove_reply())
                self.tg.send(uid, texts.MENU_TITLE, kb.main_menu())
                return
            phone = parse_phone(text)
            if not phone:
                self.tg.send(uid, "Не получилось распознать номер. Напишите, например: +7 900 123-45-67", kb.phone_request())
                return
            self.finish_lead(msg, phone)
            return
        if wait == "handoff":
            st.pop("await", None)
            self.to_admins(msg["from"], f"💬 <b>Вопрос от гостя</b>\n\n{html.escape(text)}")
            return
        if wait == "date_text":
            d = parse_date(text, now_msk().date())
            if not d:
                self.tg.send(uid, "Не понял дату. Напишите в формате дд.мм, например 28.10")
                return
            st.pop("await", None)
            st["date"] = d
            self.step_after_date(uid, None)
            return
        if wait == "hours_text":
            m = re.search(r"\d+", text)
            n = int(m.group()) if m else 0
            if not 2 <= n <= 24:
                self.tg.send(uid, "Напишите число часов от 2 до 24.")
                return
            st.pop("await", None)
            st["hours"] = n
            self.render_step(uid, None, "guests")
            return

        self.free_text(msg, text)

    # ---------- кнопки ----------
    def on_callback(self, cq: dict) -> None:
        data = cq.get("data", "")
        msg = cq.get("message") or {}
        uid = cq["from"]["id"]
        mid = msg.get("message_id")
        self.tg.answer_callback(cq["id"])
        if not msg or msg.get("chat", {}).get("type") != "private":
            return
        st = self.state.setdefault(uid, {})
        key, _, arg = data.partition(":")

        if key == "m":
            st.clear()
            self.show(uid, mid, texts.MENU_TITLE, kb.main_menu())
        elif key == "h":
            self.show(uid, mid, texts.HOURLY_INTRO, kb.hourly_list())
        elif key == "d":
            self.show(uid, mid, texts.DAILY_INTRO, kb.daily_list())
        elif key == "r":
            self.show_room(uid, mid, arg)
        elif key == "ph":
            self.send_photos(uid, arg)
        elif key == "bk":
            self.show(uid, mid, "Что бронируем?", kb.booking_choice())
        elif key == "b":
            self.start_booking(uid, mid, arg)
        elif key == "dt":
            st["date"] = datetime.strptime(arg, "%Y%m%d").date()
            self.step_after_date(uid, mid)
        elif key == "dto":
            st["await"] = "date_text"
            self.show(uid, mid, "Напишите дату в формате дд.мм, например 28.10", None)
        elif key == "t":
            st["start"] = int(arg)
            self.render_step(uid, mid, "hours")
        elif key == "hr":
            st["hours"] = int(arg)
            self.render_step(uid, mid, "guests")
        elif key == "hro":
            st["await"] = "hours_text"
            self.show(uid, mid, "Напишите, на сколько часов (от 2 до 24).", None)
        elif key == "n":
            st["nights"] = int(arg)
            self.render_step(uid, mid, "guests")
        elif key == "g":
            st["guests"] = int(arg)
            self.render_step(uid, mid, "summary")
        elif key == "back":
            self.render_step(uid, mid, arg)
        elif key == "ok":
            self.ask_phone(uid)
        elif key == "f":
            if arg in texts.FAQ:
                self.show(uid, mid, texts.FAQ[arg][1], kb.faq_section())
            else:
                self.show(uid, mid, "❓ <b>Частые вопросы</b>\n\nВыберите тему или просто напишите вопрос:", kb.faq_menu())
        elif key == "c":
            self.show(uid, mid, texts.CONTACTS, kb.contacts())
        elif key == "p":
            self.show(uid, mid, texts.PROVINCIYA, kb.provinciya())
        elif key == "a":
            st["await"] = "handoff"
            self.tg.send(uid, texts.HANDOFF_ASK)

    def show(self, uid, mid, text, markup):
        """Меню меняем в том же сообщении, чтобы чат не засорялся."""
        if mid:
            try:
                self.tg.edit(uid, mid, text, markup)
                return
            except ChannelError:
                pass
        self.tg.send(uid, text, markup)

    # ---------- залы и фото ----------
    def photos_for(self, room: str):
        folder = config.MEDIA_DIR / room
        if not folder.is_dir():
            return []
        return sorted(p for p in folder.iterdir() if p.suffix.lower() in PHOTO_EXT)

    def show_room(self, uid, mid, room):
        if room not in texts.ROOM_CARDS:
            return
        self.show(uid, mid, texts.ROOM_CARDS[room],
                  kb.room_card(room, bool(self.photos_for(room)), texts.ROOM_PAGES.get(room, texts.SITE)))

    def send_photos(self, uid, room):
        files = self.photos_for(room)
        if not files:
            url = texts.ROOM_PAGES.get(room, texts.SITE)
            self.tg.send(uid, f"Фото «{texts.room_name(room)}» можно посмотреть на сайте: {url}")
            return
        key = f"{getattr(self.tg, 'name', 'tg')}:{room}"
        cached = self.db.photo_ids(key)
        try:
            if len(cached) == len(files[:10]):
                self.tg.send_photos(uid, cached)
            else:
                self.db.save_photo_ids(key, self.tg.send_photos(uid, files))
        except ChannelError:
            self.db.save_photo_ids(key, self.tg.send_photos(uid, files))
        self.tg.send(uid, f"Это «{texts.room_name(room)}». Посчитать стоимость?",
                     kb.ikb([[("📅 Рассчитать и забронировать", f"b:{room}")], [("↩ Меню", "m")]]))

    # ---------- бронирование ----------
    def start_booking(self, uid, mid, room):
        if room in HOURLY["rooms"] and not HOURLY["rooms"][room]["open"]:
            self.show_room(uid, mid, room)
            return
        if room not in HOURLY["rooms"] and room not in DAILY["rooms"]:
            return
        self.state[uid] = {"room": room, "kind": "hourly" if room in HOURLY["rooms"] else "daily"}
        self.render_step(uid, mid, "date")

    def step_after_date(self, uid, mid):
        st = self.state[uid]
        self.render_step(uid, mid, "time" if st.get("kind") == "hourly" else "nights")

    def header(self, st) -> str:
        parts = [f"<b>{texts.room_name(st['room'])}</b>"]
        if st.get("date"):
            parts.append(day_label(st["date"]))
        if st.get("start") is not None and st.get("kind") == "hourly":
            parts.append(f"с {st['start']:02d}:00")
        if st.get("hours"):
            parts.append(f"{st['hours']} ч")
        if st.get("nights"):
            parts.append(f"{st['nights']} н.")
        return " · ".join(parts)

    def render_step(self, uid, mid, step):
        st = self.state.get(uid) or {}
        if "room" not in st:
            self.show(uid, mid, texts.MENU_TITLE, kb.main_menu())
            return
        room, hourly = st["room"], st["kind"] == "hourly"
        if step == "date":
            title = "📅 Выберите дату:" if hourly else "📅 Выберите дату заезда:"
            self.show(uid, mid, f"{self.header(st)}\n\n{title}", kb.dates(now_msk().date(), back=f"r:{room}"))
        elif step == "time":
            self.show(uid, mid, f"{self.header(st)}\n\n🕐 Во сколько начало?", kb.start_times())
        elif step == "hours":
            self.show(uid, mid, f"{self.header(st)}\n\n⏳ На сколько часов? Минимум 2.", kb.durations())
        elif step == "nights":
            self.show(uid, mid, f"{self.header(st)}\n\n🌙 Сколько ночей?", kb.nights())
        elif step == "guests":
            max_g = (HOURLY if hourly else DAILY)["rooms"][room]["max_guests"]
            back = "back:hours" if hourly else "back:nights"
            self.show(uid, mid, f"{self.header(st)}\n\n👥 Сколько будет гостей, включая детей?", kb.guests(max_g, back))
        elif step == "summary":
            try:
                if hourly:
                    q = quote_hourly(room, st["date"], st["start"], st["hours"], st["guests"])
                else:
                    q = quote_daily(room, st["date"], st["nights"], st["guests"])
            except (PriceError, KeyError) as e:
                self.show(uid, mid, f"⚠️ {e}", kb.ikb([[("← Изменить", "back:date"), ("↩ Меню", "m")]]))
                return
            st["quote"] = q
            if hourly:
                note = "\n\nБронь подтверждает администратор и присылает ссылку на предоплату."
                self.show(uid, mid, "Проверьте, всё ли верно:\n\n" + q.text() + note, kb.confirm_hourly())
            else:
                note = "\n\nЗабронировать и оплатить можно сразу онлайн или оставить заявку администратору."
                self.show(uid, mid, "Проверьте, всё ли верно:\n\n" + q.text() + note, kb.confirm_daily())

    def ask_phone(self, uid):
        st = self.state.get(uid) or {}
        if "quote" not in st:
            self.tg.send(uid, texts.MENU_TITLE, kb.main_menu())
            return
        st["await"] = "phone"
        consent = "Отправляя номер, Вы соглашаетесь на обработку персональных данных"
        consent += f" (<a href=\"{config.PRIVACY_URL}\">политика</a>)." if config.PRIVACY_URL else "."
        self.tg.send(uid, "Оставьте, пожалуйста, номер телефона — администратор подтвердит бронь.\n\n"
                          f"<i>{consent}</i>", kb.phone_request())

    def finish_lead(self, msg, phone):
        uid = msg["chat"]["id"]
        st = self.state.get(uid) or {}
        q = st.get("quote")
        st.pop("await", None)
        if not q:
            return
        phone = parse_phone(phone) or phone
        lead_id = self.db.add_lead(uid, st["kind"], {
            "room": st["room"], "date": st["date"].isoformat(), "start": st.get("start"),
            "hours": st.get("hours"), "nights": st.get("nights"), "guests": st["guests"],
            "total": q.total, "phone": phone,
        })
        body = q.text().replace("💰 ", "")
        text = (f"🆕 <b>Заявка №{lead_id} из бота</b>\n\n{body}\n\n"
                f"👤 {self.tg.user_ref(msg['from'])}\n📞 {html.escape(phone)}\n\n"
                "<i>Ответьте на это сообщение — бот перешлёт ответ гостю.</i>")
        sent = self.to_admins(msg["from"], text, silent=True)
        self.state[uid] = {}
        self.tg.send(uid, "Заявка отправлена ✅", kb.remove_reply())
        if sent:
            extra = (" и пришлёт ссылку на предоплату 2 000 ₽" if st["kind"] == "hourly" else "")
            self.tg.send(uid, f"Администратор свяжется с Вами в ближайшее время, подтвердит бронь{extra}.",
                         kb.after_lead())
        else:
            self.tg.send(uid, texts.NO_ADMIN_CHAT, kb.after_lead())

    # ---------- администраторы ----------
    def to_admins(self, user: dict, text: str, silent: bool = False) -> bool:
        uid = user["id"]
        if not config.ADMIN_CHAT_ID:
            log.warning("ADMIN_CHAT_ID не задан, сообщение не отправлено: %s", text)
            if not silent:
                self.tg.send(uid, texts.NO_ADMIN_CHAT)
            return False
        if "👤" not in text:
            text += f"\n\n👤 {self.tg.user_ref(user)}\n<i>Ответьте на это сообщение — бот перешлёт ответ гостю.</i>"
        sent = self.tg.send(config.ADMIN_CHAT_ID, text)
        self.db.link(sent["message_id"], uid)
        if not silent:
            self.tg.send(uid, texts.HANDOFF_DONE)
        return True

    def on_receipt(self, msg):
        uid = msg["chat"]["id"]
        self.tg.send(uid, texts.RECEIPT_REPLY)
        if not config.ADMIN_CHAT_ID:
            return
        head = self.tg.send(config.ADMIN_CHAT_ID, f"🧾 <b>Гость прислал чек или файл</b>\n👤 {self.tg.user_ref(msg['from'])}\n"
                                                  "<i>Проверьте оплату в ЮKassa. Ответьте на это сообщение, чтобы написать гостю.</i>")
        self.db.link(head["message_id"], uid)
        fwd_id = self.tg.forward(config.ADMIN_CHAT_ID, uid, msg)
        if fwd_id:
            self.db.link(fwd_id, uid)

    def on_admin_message(self, msg):
        reply = msg.get("reply_to_message")
        if not reply:
            return
        uid = self.db.user_for(reply["message_id"])
        if not uid:
            return
        try:
            self.tg.copy(uid, msg)
            self.tg.ack(msg)
        except ChannelError as e:
            self.tg.send(msg["chat"]["id"], f"Не удалось отправить гостю: {html.escape(str(e))}",
                         reply_to=msg["message_id"])

    # ---------- свободный текст ----------
    def free_text(self, msg, text):
        uid = msg["chat"]["id"]
        if self.ai:
            try:
                self.tg.typing(uid)
                reply, actions = self.ai.answer(uid, text, now_msk().date())
            except Exception:
                log.exception("Ошибка ИИ, переключаюсь на ключевые слова")
            else:
                if reply:
                    self.tg.send(uid, reply)
                for name, arg in actions:
                    if name == "show_photos":
                        self.send_photos(uid, arg)
                    elif name == "start_booking":
                        self.start_booking(uid, None, arg)
                    elif name == "call_admin":
                        self.to_admins(msg["from"], f"💬 <b>Гость просит администратора</b>\n\n{html.escape(arg)}\n\n"
                                                    f"Последнее сообщение: «{html.escape(text)}»", silent=True)
                return
        self.keyword_reply(msg, text)

    def keyword_reply(self, msg, text):
        uid = msg["chat"]["id"]
        low = " " + text.lower() + " "
        for words, (kind, arg) in KEYWORDS:
            if any(w in low for w in words):
                if kind == "room":
                    self.show_room(uid, None, arg)
                elif kind == "faq":
                    self.tg.send(uid, texts.FAQ[arg][1], kb.faq_section())
                elif kind == "prov":
                    self.tg.send(uid, texts.PROVINCIYA, kb.provinciya())
                elif kind == "contacts":
                    self.tg.send(uid, texts.CONTACTS, kb.contacts())
                elif kind == "admin":
                    self.state.setdefault(uid, {})["await"] = "handoff"
                    self.tg.send(uid, texts.HANDOFF_ASK)
                elif kind == "book":
                    self.tg.send(uid, "Посчитаю стоимость за минуту. Что бронируем?", kb.booking_choice())
                elif kind == "halls":
                    self.tg.send(uid, texts.HOURLY_INTRO, kb.hourly_list())
                return
        self.tg.send(uid, texts.FALLBACK, kb.fallback())
