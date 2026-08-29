from __future__ import annotations

from html import escape

from app.models import Vacancy

DECISION_LABELS = {
    "strong_apply": "откликнуться в первую очередь",
    "apply": "стоит откликнуться",
    "maybe": "сначала проверить условия",
    "skip": "лучше пропустить",
}
ROLE_LEVEL_LABELS = {
    "intern": "Стажёр",
    "junior": "Junior",
    "junior_plus": "Junior+",
    "middle": "Middle",
    "senior": "Senior",
    "lead": "Lead",
    "unknown": "Не определён",
}
CURRENCY_SYMBOLS = {"RUR": "₽", "RUB": "₽", "USD": "$", "EUR": "€"}
STATUS_LABELS = {
    "viewed": "👁 Просмотрена",
    "saved": "❤️ В избранном",
    "applied_manual": "✅ Вы откликнулись самостоятельно",
    "applied_bot": "✅ Отклик отправлен через бота",
    "interview": "🤝 Интервью",
    "test_task": "🧪 Тестовое задание",
    "rejected": "⛔ Отказ",
    "offer": "🎉 Оффер",
    "offer_accepted": "🏁 Оффер принят",
    "hidden": "🙈 Скрыта",
    "archived": "🗄 В архиве",
}


def _safe(value: object, limit: int = 500) -> str:
    text = str(value or "Не указано")
    result: list[str] = []
    length = 0
    for character in text:
        encoded = escape(character)
        if length + len(encoded) > limit - 1:
            result.append("…")
            break
        result.append(encoded)
        length += len(encoded)
    return "".join(result)


def format_salary(vacancy: Vacancy) -> str:
    salary_from = vacancy.salary_from
    salary_to = vacancy.salary_to
    if salary_from is None and salary_to is None:
        return "не указана"
    symbol = CURRENCY_SYMBOLS.get(
        vacancy.salary_currency or "", vacancy.salary_currency or ""
    )
    if salary_from is not None and salary_to is not None:
        value = f"{salary_from:,}–{salary_to:,}".replace(",", " ")
    elif salary_from is not None:
        value = f"от {salary_from:,}".replace(",", " ")
    else:
        value = f"до {salary_to:,}".replace(",", " ")
    tax = " до вычета налогов" if vacancy.salary_gross else ""
    return f"{value} {symbol}{tax}".strip()


def _list_section(
    title: str,
    values: list[str],
    *,
    max_items: int = 6,
    item_limit: int = 220,
) -> str:
    if not values:
        return ""
    items = "\n".join(f"• {_safe(item, item_limit)}" for item in values[:max_items])
    return f"<b>{title}:</b>\n{items}"


def _score_bar(score: int) -> str:
    filled = max(0, min(10, round(score / 10)))
    return "●" * filled + "○" * (10 - filled)


def _summary(vacancy: Vacancy, limit: int) -> str:
    text = vacancy.description or vacancy.requirements or vacancy.responsibilities
    if not text:
        return "Описание появится на странице вакансии."
    return _safe(text, limit)


def _stack(vacancy: Vacancy) -> str:
    skills = vacancy.key_skills
    if not skills and vacancy.analysis is not None:
        skills = vacancy.analysis.matched_skills
    if not skills:
        return "не указан"
    visible = " · ".join(skills[:8])
    if len(skills) > 8:
        visible += " · …"
    return visible


def _published(vacancy: Vacancy) -> str:
    if vacancy.published_at is None:
        return "не указана"
    return vacancy.published_at.strftime("%d.%m.%Y")


def format_vacancy_card(
    vacancy: Vacancy,
    *,
    position: int = 1,
    total: int = 1,
    collection_title: str = "Вакансия",
    expanded: bool = False,
) -> str:
    analysis = vacancy.analysis
    if analysis is None:
        return f"<b>{_safe(vacancy.title)} — {_safe(vacancy.company)}</b>"
    status = vacancy.application.status if vacancy.application else "new"
    status_line = STATUS_LABELS.get(status)
    header = (
        f"<b>💼 {_safe(collection_title, 120)}</b>\n<code>{position} / {total}</code>"
    )
    identity = f"<b>{_safe(vacancy.title, 420)}</b>\n🏢 {_safe(vacancy.company, 300)}"
    facts = (
        f"💰 <b>Зарплата:</b> {_safe(format_salary(vacancy))}\n"
        f"📍 <b>Город:</b> {_safe(vacancy.location)}\n"
        f"🧭 <b>Формат:</b> {_safe(vacancy.work_format)}\n"
        f"🧑‍💻 <b>Опыт:</b> {_safe(vacancy.experience)}\n"
        f"🧩 <b>Стек:</b> {_safe(_stack(vacancy), 650)}"
    )
    score = (
        f"<b>✨ Совпадение — {analysis.match_score}%</b>\n"
        f"<code>{_score_bar(analysis.match_score)}</code>\n"
        "🎯 <b>Рекомендация:</b> "
        f"{_safe(DECISION_LABELS.get(analysis.decision, analysis.decision), 180)}"
    )
    gaps = analysis.blocking_requirements or analysis.missing_skills
    gap_title = (
        "🚧 Критичные препятствия" if analysis.blocking_requirements else "📚 Пробелы"
    )
    blocks = [
        header,
        identity,
        status_line or "",
        facts,
        score,
        f"<b>🤖 Почему подходит</b>\n{_safe(analysis.reason, 520)}",
    ]
    if expanded:
        blocks.extend(
            [
                f"<b>Описание вакансии</b>\n{_summary(vacancy, 1_100)}",
                (
                    "<b>Детали вакансии:</b>\n"
                    "• Уровень: "
                    f"{_safe(ROLE_LEVEL_LABELS.get(analysis.role_level, analysis.role_level), 80)}\n"
                    f"• Занятость: {_safe(vacancy.employment, 160)}\n"
                    f"• Опубликована: {_safe(_published(vacancy), 40)}"
                ),
                _list_section("✅ Совпало", analysis.matched_skills, max_items=8),
                _list_section(
                    "🚧 Обязательные блокеры",
                    analysis.blocking_requirements,
                    max_items=5,
                ),
                _list_section(
                    "📚 Стоит подтянуть", analysis.missing_skills, max_items=6
                ),
                _list_section("💪 Преимущества", analysis.advantages, max_items=5),
                _list_section("⚠️ Риски", analysis.risks, max_items=5),
                _list_section(
                    "✉️ Что подчеркнуть в письме",
                    analysis.resume_focus,
                    max_items=5,
                ),
            ]
        )
    else:
        blocks.extend(
            [
                _list_section(
                    "✅ Сильные совпадения",
                    analysis.matched_skills,
                    max_items=3,
                    item_limit=160,
                ),
                _list_section(gap_title, gaps, max_items=2, item_limit=180),
                _list_section(
                    "⚠️ Red flags",
                    analysis.risks,
                    max_items=2,
                    item_limit=180,
                ),
                _list_section(
                    "✉️ Что подчеркнуть в письме",
                    analysis.resume_focus,
                    max_items=2,
                    item_limit=180,
                ),
            ]
        )
    selected: list[str] = []
    for block in (block for block in blocks if block):
        candidate = "\n\n".join([*selected, block])
        if len(candidate) > 3950:
            break
        selected.append(block)
    return "\n\n".join(selected)


def split_plain_text(text: str, limit: int = 3500) -> list[str]:
    chunks: list[str] = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        position = remaining.rfind("\n", 0, limit)
        if position < limit // 2:
            position = remaining.rfind(" ", 0, limit)
        if position < limit // 2:
            position = limit
        chunks.append(remaining[:position].strip())
        remaining = remaining[position:].strip()
    return chunks
