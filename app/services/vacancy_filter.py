from __future__ import annotations

import re
from typing import Protocol

from app.schemas import VacancyFilterResult


class VacancyText(Protocol):
    title: str
    description: str
    requirements: str
    responsibilities: str


POSITIVE_PATTERNS: dict[str, str] = {
    "iOS": r"\bios\b|айос",
    "Swift": r"\bswift\b",
    "SwiftUI": r"\bswiftui\b",
    "UIKit": r"\buikit\b",
    "Apple mobile": r"мобильн\w*\s+разработ\w*\s+(?:под|для)\s+apple|apple\s+mobile",
    "Junior": r"\bjunior\+?\b|\bjun\b|джун\w*",
    "Intern": r"стаж[её]р\w*|стажиров\w*|\bintern(?:ship)?\b",
    "Mobile developer": r"разработ\w*\s+мобильн\w*\s+прилож\w*|\bmobile\s+(?:app\s+)?developer\b",
}


class VacancyFilter:
    def evaluate(self, vacancy: VacancyText) -> VacancyFilterResult:
        title = (vacancy.title or "").lower()
        full_text = "\n".join(
            [
                vacancy.title or "",
                vacancy.description or "",
                vacancy.requirements or "",
                vacancy.responsibilities or "",
            ]
        ).lower()
        positives = [
            label
            for label, pattern in POSITIVE_PATTERNS.items()
            if re.search(pattern, full_text, flags=re.IGNORECASE)
        ]
        negatives: list[str] = []
        reasons: list[str] = []

        ios_context = bool(re.search(r"\bios\b|\bswift(?:ui)?\b|\buikit\b", full_text))
        if re.search(
            r"\bsenior\b|\bsr\.?\s+(?:ios|developer)|\blead\b|team\s*lead", title
        ):
            negatives.append("Senior/Lead")
            reasons.append("Уровень Senior или Lead указан в названии вакансии")
        if re.search(r"руководител\w*|начальник\w*|head\s+of", title):
            negatives.append("Руководящая позиция")
            reasons.append("Вакансия предполагает руководящую роль")
        if re.search(r"\bархитектор\b|\barchitect\b", title):
            negatives.append("Архитектор")
            reasons.append("Это позиция архитектора, а не junior iOS-разработчика")
        if re.search(
            r"\b(?:qa|tester|designer|recruiter|product\s+manager|project\s+manager)\b|"
            r"тестировщик\w*|дизайнер\w*|аналитик\w*|рекрутер\w*|маркетолог\w*",
            title,
        ):
            negatives.append("Не разработка")
            reasons.append("Вакансия не относится к разработке приложений")

        if re.search(r"\bandroid\b|\bkotlin\b", full_text) and not ios_context:
            negatives.append("Android/Kotlin без iOS")
            reasons.append("Упомянуты Android или Kotlin без iOS/Swift")

        years_patterns = (
            r"(?:не\s+менее|минимум|от)\s*([4-9]|\d{2,})\s*(?:лет|год)",
            r"([4-9]|\d{2,})\s*\+\s*(?:лет|год|years?)",
            r"(?:at\s+least|minimum)\s*([4-9]|\d{2,})\+?\s*years?",
            r"(?:опыт\w*|experience)\D{0,25}([4-9]|\d{2,})\s*[-–—]\s*\d+\s*(?:лет|years?)",
            r"([4-9]|\d{2,})\s*[-–—]\s*\d+\s*(?:лет|years?)\D{0,20}(?:опыт\w*|experience)",
            r"обязательн\w*\D{0,20}([4-9]|\d{2,})\s*(?:лет|год|years?)",
        )
        if any(re.search(pattern, full_text) for pattern in years_patterns):
            negatives.append("Обязательный опыт 4+ года")
            reasons.append("Требуется не менее четырех лет опыта")

        if re.search(
            r"только\s+objective[ -]?c|objective[ -]?c\s+only|exclusively\s+objective[ -]?c",
            full_text,
        ):
            negatives.append("Только Objective-C")
            reasons.append("Вакансия требует только Objective-C")

        if re.search(
            r"обязательн\w*\s+релокац\w*|релокац\w*.{0,20}обязательн\w*|must\s+relocate",
            full_text,
        ):
            negatives.append("Обязательная релокация")
            reasons.append("Указана обязательная релокация")

        if not positives:
            negatives.append("Нет iOS/Swift-контекста")
            reasons.append("Не найдены признаки iOS/Swift-разработки")

        if positives and not negatives:
            reasons.append("Обнаружен релевантный iOS/Swift-контекст")

        return VacancyFilterResult(
            is_relevant=not negatives,
            reasons=reasons,
            detected_positive_keywords=positives,
            detected_negative_keywords=negatives,
        )

    def filter(self, vacancy: VacancyText) -> VacancyFilterResult:
        return self.evaluate(vacancy)
