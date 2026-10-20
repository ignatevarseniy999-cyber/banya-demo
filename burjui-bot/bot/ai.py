"""Ответы на свободный текст через Claude. Цены считает код (инструменты), ИИ только разговаривает."""
import json
import logging
from datetime import date

from . import config
from .config import DATA_DIR
from .pricing import DAILY, HOURLY, PriceError, day_label, quote_daily, quote_hourly

log = logging.getLogger(__name__)

KB = (DATA_DIR / "baza_znaniy.md").read_text(encoding="utf-8")

SYSTEM = f"""Ты — «Буржуй клаб, помощник», чат-бот банного комплекса «Буржуй клаб» в Подольске в Telegram.

Как отвечать:
- Обращайся к гостю на «Вы». Пиши коротко и тепло: 1–4 предложения, без длинных списков, если гость сам не просит подробностей.
- Не используй иностранные слова. Можно 1 уместный смайлик.
- Отвечай только по базе знаний ниже. Если ответа в базе нет — не придумывай, а вызови call_admin.
- Цены никогда не считай сам. Для расчёта почасовой аренды вызывай quote_hourly, для посуточной — quote_daily, и называй гостю ровно ту сумму, что вернул инструмент.
- Если для расчёта не хватает данных (дата, время, сколько часов, сколько гостей) — спроси недостающее одним вопросом.
- Когда гость готов бронировать или просит забронировать — вызови start_booking: бот покажет кнопки выбора даты и времени.
- Если гость просит фото — вызови show_photos.
- Зови администратора (call_admin) в случаях из раздела 10 базы знаний: праздник или корпоратив, торг и скидки, жалоба, возврат денег, просьба позвать человека, забытые вещи, всё про Провинцию и бронь Монарха.
- Монарх закрыт на ремонт — не бронируй его, предложи Герцог.
- Не обещай, что бронь подтверждена: бронь подтверждает администратор.
- Форматирование: только обычный текст, можно <b>жирный</b>. Без markdown-звёздочек и решёток.

<база_знаний>
{KB}
</база_знаний>"""

ROOM_IDS = list(HOURLY["rooms"]) + list(DAILY["rooms"])
ROOM_HELP = ", ".join(f"{rid} — {r['name']}" for rid, r in {**HOURLY["rooms"], **DAILY["rooms"]}.items())


def _tool(name, description, props, required):
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {"type": "object", "properties": props, "required": required, "additionalProperties": False},
    }


TOOLS = [
    _tool("quote_hourly", "Точная стоимость почасовой аренды зала с баней (Барон, Маркиз, Герцог, Монарх).", {
        "room": {"type": "string", "enum": list(HOURLY["rooms"]), "description": ROOM_HELP},
        "date": {"type": "string", "description": "Дата начала в формате YYYY-MM-DD"},
        "start_hour": {"type": "integer", "description": "Час начала, 0–23. Если гость не сказал — 18"},
        "hours": {"type": "integer", "description": "Сколько часов"},
        "guests": {"type": "integer", "description": "Сколько гостей, включая детей"},
    }, ["room", "date", "start_hour", "hours", "guests"]),
    _tool("quote_daily", "Точная стоимость посуточного проживания (Виконт, студия).", {
        "room": {"type": "string", "enum": list(DAILY["rooms"]), "description": ROOM_HELP},
        "check_in": {"type": "string", "description": "Дата заезда YYYY-MM-DD"},
        "nights": {"type": "integer"},
        "guests": {"type": "integer"},
    }, ["room", "check_in", "nights", "guests"]),
    _tool("start_booking", "Показать гостю кнопки бронирования выбранного зала или жилья.", {
        "room": {"type": "string", "enum": ROOM_IDS, "description": ROOM_HELP},
    }, ["room"]),
    _tool("show_photos", "Отправить гостю фото зала или жилья.", {
        "room": {"type": "string", "enum": ROOM_IDS, "description": ROOM_HELP},
    }, ["room"]),
    _tool("call_admin", "Передать разговор администратору.", {
        "summary": {"type": "string", "description": "Кратко для администратора: что хочет гость, даты, число гостей"},
    }, ["summary"]),
]


class Assistant:
    """Хранит короткую историю (только текст) и ведёт цикл с инструментами."""

    def __init__(self):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self.history: dict[int, list] = {}

    def _run_tool(self, name, args, actions):
        try:
            if name == "quote_hourly":
                q = quote_hourly(args["room"], date.fromisoformat(args["date"]), args["start_hour"] % 24,
                                 args["hours"], args["guests"])
                return {"total": q.total, "prepayment": q.prepayment, "details": q.text()}
            if name == "quote_daily":
                q = quote_daily(args["room"], date.fromisoformat(args["check_in"]), args["nights"], args["guests"])
                return {"total": q.total, "deposit": q.deposit, "details": q.text(),
                        "booking_link": DAILY["booking_link"]}
            if name in ("start_booking", "show_photos"):
                actions.append((name, args["room"]))
                return {"ok": True, "note": "Кнопки/фото будут показаны гостю после твоего ответа."}
            if name == "call_admin":
                actions.append((name, args["summary"]))
                return {"ok": True, "note": "Администратор получил сообщение и ответит гостю в этом чате."}
        except PriceError as e:
            return {"error": str(e)}
        except (ValueError, KeyError) as e:
            return {"error": f"Неверные данные: {e}"}
        return {"error": "Неизвестный инструмент"}

    def answer(self, user_id: int, text: str, today: date):
        """Возвращает (текст ответа, список действий)."""
        hist = self.history.setdefault(user_id, [])
        user_content = [
            {"type": "text", "text": f"[Сегодня {day_label(today)} {today.year}, {today.isoformat()}]"},
            {"type": "text", "text": text},
        ]
        messages = hist + [{"role": "user", "content": user_content}]
        actions: list = []
        reply = ""
        for _ in range(5):
            resp = self.client.beta.messages.create(
                model=config.AI_MODEL,
                max_tokens=4000,
                system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                tools=TOOLS,
                messages=messages,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if resp.stop_reason == "refusal":
                actions.append(("call_admin", f"Бот не смог ответить на сообщение: {text[:300]}"))
                reply = "Передам этот вопрос администратору, он ответит Вам здесь."
                break
            messages.append({"role": "assistant", "content": resp.content})
            reply = "".join(b.text for b in resp.content if b.type == "text").strip()
            if resp.stop_reason != "tool_use":
                break
            results = []
            for b in resp.content:
                if b.type == "tool_use":
                    out = self._run_tool(b.name, b.input, actions)
                    results.append({"type": "tool_result", "tool_use_id": b.id,
                                    "content": json.dumps(out, ensure_ascii=False)})
            messages.append({"role": "user", "content": results})
        # В историю кладём только текст: короткая, без служебных блоков, обрезаем по 10 сообщений
        hist.append({"role": "user", "content": text})
        hist.append({"role": "assistant", "content": reply or "…"})
        del hist[:-10]
        return reply, actions

    def reset(self, user_id: int) -> None:
        self.history.pop(user_id, None)
