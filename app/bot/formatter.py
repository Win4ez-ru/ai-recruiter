from __future__ import annotations

from html import escape

from app.models import Vacancy

DECISION_LABELS = {
    "strong_apply": "обязательно откликнуться",
    "apply": "стоит откликнуться",
    "maybe": "можно рассмотреть",
    "skip": "лучше пропустить",
}
CURRENCY_SYMBOLS = {"RUR": "₽", "RUB": "₽", "USD": "$", "EUR": "€"}


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


def _list_section(title: str, values: list[str], *, max_items: int = 6) -> str:
    if not values:
        return ""
    items = "\n".join(f"• {_safe(item, 220)}" for item in values[:max_items])
    return f"<b>{title}:</b>\n{items}"


def format_vacancy_card(vacancy: Vacancy) -> str:
    analysis = vacancy.analysis
    if analysis is None:
        return f"<b>{_safe(vacancy.title)} — {_safe(vacancy.company)}</b>"
    blocks = [
        f"<b>{_safe(vacancy.title)} — {_safe(vacancy.company)}</b>",
        (
            f"<b>Оценка:</b> {analysis.match_score}/100\n"
            f"<b>Решение:</b> {_safe(DECISION_LABELS.get(analysis.decision, analysis.decision))}"
        ),
        (
            f"<b>Зарплата:</b> {_safe(format_salary(vacancy))}\n"
            f"<b>Формат:</b> {_safe(vacancy.work_format)}\n"
            f"<b>Город:</b> {_safe(vacancy.location)}\n"
            f"<b>Опыт:</b> {_safe(vacancy.experience)}"
        ),
        _list_section("Совпало", analysis.matched_skills),
        _list_section("Не хватает", analysis.missing_skills),
        _list_section("Преимущества", analysis.advantages),
        _list_section("Риски", analysis.risks),
        _list_section("На чем сделать акцент", analysis.resume_focus),
        f"<b>Почему стоит откликнуться:</b>\n{_safe(analysis.reason, 800)}",
    ]
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
