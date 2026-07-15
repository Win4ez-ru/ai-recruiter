from app.schemas import VacancyCreate
from app.services.vacancy_filter import VacancyFilter


def vacancy(title: str, description: str, **kwargs: object) -> VacancyCreate:
    return VacancyCreate(
        external_id=str(kwargs.pop("external_id", "1")),
        title=title,
        url="https://hh.ru/vacancy/1",
        description=description,
        **kwargs,
    )


def test_junior_ios_vacancy_is_relevant() -> None:
    result = VacancyFilter().evaluate(
        vacancy(
            "Junior iOS Developer",
            "Разработка приложения на Swift и SwiftUI. Коммерческий опыт желателен.",
        )
    )
    assert result.is_relevant is True
    assert "iOS" in result.detected_positive_keywords


def test_senior_android_vacancy_is_rejected() -> None:
    result = VacancyFilter().evaluate(
        vacancy("Senior Android Developer", "Kotlin, Android, опыт от 5 лет")
    )
    assert result.is_relevant is False
    assert "Senior/Lead" in result.detected_negative_keywords
    assert "Android/Kotlin без iOS" in result.detected_negative_keywords


def test_uikit_and_one_to_three_years_are_not_auto_rejected() -> None:
    result = VacancyFilter().evaluate(
        vacancy(
            "iOS-разработчик",
            "Swift, UIKit, Combine. Требуется опыт 1–3 года.",
        )
    )
    assert result.is_relevant is True
    assert "UIKit" in result.detected_positive_keywords


def test_vacancy_without_salary_passes_filter() -> None:
    item = vacancy("Стажер iOS", "SwiftUI, помощь в разработке мобильного приложения")
    assert item.salary_from is None and item.salary_to is None
    assert VacancyFilter().evaluate(item).is_relevant is True


def test_mandatory_four_to_five_years_are_rejected() -> None:
    result = VacancyFilter().evaluate(
        vacancy(
            "iOS Developer",
            "Обязательно 4–5 лет коммерческого опыта со Swift.",
        )
    )
    assert result.is_relevant is False
    assert "Обязательный опыт 4+ года" in result.detected_negative_keywords


def test_ios_product_manager_is_not_treated_as_developer() -> None:
    result = VacancyFilter().evaluate(
        vacancy("Product Manager iOS", "Управление развитием iOS-продукта")
    )
    assert result.is_relevant is False
    assert "Не разработка" in result.detected_negative_keywords
