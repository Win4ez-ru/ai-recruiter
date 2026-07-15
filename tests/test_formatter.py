from app.bot.formatter import format_vacancy_card
from app.models import Vacancy, VacancyAnalysis


def make_vacancy() -> Vacancy:
    item = Vacancy(
        id=1,
        source="hh",
        external_id="1",
        title="iOS_[Dev] <Junior>",
        company="R&D & Partners",
        url="https://hh.ru/vacancy/1",
        description="",
        requirements="",
        responsibilities="",
        key_skills=[],
        location="Санкт-Петербург",
        work_format="Удаленно",
        experience="1–3 года",
        employment="Полная занятость",
    )
    item.analysis = VacancyAnalysis(
        vacancy_id=1,
        match_score=84,
        decision="apply",
        role_level="junior_plus",
        matched_skills=["Swift", "SwiftUI"],
        missing_skills=["Combine"],
        blocking_requirements=[],
        advantages=["Два проекта"],
        risks=["Нет коммерческого опыта"],
        resume_focus=["Pump Kitchen"],
        reason="Большая часть требований совпадает.",
        model_name="test-model",
    )
    return item


def test_formatter_handles_missing_salary() -> None:
    text = format_vacancy_card(make_vacancy())
    assert "Зарплата:</b> не указана" in text


def test_formatter_escapes_external_text_and_markdown_characters() -> None:
    text = format_vacancy_card(make_vacancy())
    assert "iOS_[Dev]" in text
    assert "&lt;Junior&gt;" in text
    assert "R&amp;D &amp; Partners" in text
    assert "<Junior>" not in text


def test_formatter_keeps_very_long_external_text_within_telegram_limit() -> None:
    item = make_vacancy()
    item.title = "<&" * 2_000
    item.company = "Company" * 1_000
    item.analysis.reason = "Очень длинная причина & <риск> " * 1_000
    text = format_vacancy_card(item)
    assert text
    assert len(text) <= 4000
    assert text.count("<b>") == text.count("</b>")
