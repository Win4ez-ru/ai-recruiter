from __future__ import annotations

from html import escape

from app.models import Vacancy
from app.schemas import CandidateProfile, PreparedApplication, StatsResult

STATUS_NAMES = {
    "new": "Новая",
    "viewed": "Просмотрена",
    "saved": "В избранном",
    "applied_manual": "Самостоятельный отклик",
    "applied_bot": "Отклик через бота",
    "interview": "Интервью",
    "test_task": "Тестовое задание",
    "rejected": "Отказ",
    "offer": "Оффер",
    "offer_accepted": "Оффер принят",
    "hidden": "Скрыта",
    "archived": "В архиве",
}


def _safe(value: str, limit: int) -> str:
    result: list[str] = []
    length = 0
    for character in value:
        encoded = escape(character)
        if length + len(encoded) > limit - 1:
            result.append("…")
            break
        result.append(encoded)
        length += len(encoded)
    return "".join(result)


def main_menu_text(
    profile: CandidateProfile,
    *,
    hh_connected: bool,
    ai_provider: str,
    new_count: int,
    demo_mode: bool,
    notice: str | None = None,
) -> str:
    hh_status = "подключён" if hh_connected else "не подключён"
    ai_name = {
        "yandex": "YandexGPT",
        "ollama": "Ollama (локально)",
        "openai": "OpenAI",
    }.get(ai_provider, ai_provider)
    notice_block = f"💬 <i>{escape(notice)}</i>\n\n" if notice else ""
    mode_block = (
        "\n🧪 <b>Демо-режим:</b> внешняя отправка отключена"
        if demo_mode
        else "\n📤 <b>Реальные отклики:</b> после двойного подтверждения"
    )
    return (
        "<b>👋 AI Recruiter</b>\n"
        "<i>Персональный поиск вакансий</i>\n\n"
        f"{notice_block}"
        f"Ищу роли для <b>{escape(profile.candidate_name)}</b> и помогаю "
        "быстро оценивать и отправлять отклики.\n\n"
        f"👤 <b>Профиль:</b> готов\n"
        f"🤖 <b>AI-модель:</b> {escape(ai_name)}\n"
        f"🔐 <b>HeadHunter:</b> {hh_status}\n"
        f"✨ <b>Непросмотренных:</b> {new_count}{mode_block}\n\n"
        "Выберите раздел ниже."
    )


def help_text() -> str:
    return (
        "<b>💡 Как это работает</b>\n\n"
        "<b>1.</b> Запустите поиск — бот соберёт свежие вакансии.\n"
        "<b>2.</b> Получите Top-5 с объяснением совпадений, пробелов и рисков.\n"
        "<b>3.</b> Листайте карточки и сохраняйте интересное.\n"
        "<b>4.</b> Проверьте резюме и персональное письмо.\n"
        "<b>5.</b> Реальный отклик уйдёт только после второго подтверждения.\n\n"
        "Если HH временно не ответит, черновик письма и выбранное резюме "
        "останутся сохранены.\n\n"
        "Весь интерфейс живёт в одном сообщении. Команды остаются доступны, "
        "но удобнее пользоваться кнопками."
    )


def profile_text(profile: CandidateProfile) -> str:
    skills = " · ".join(escape(skill) for skill in profile.strong_skills[:10])
    roles = " · ".join(escape(role) for role in profile.target_roles)
    minimum_salary = f"{profile.minimum_salary_rub:,}".replace(",", " ")
    return (
        f"<b>👤 {escape(profile.candidate_name)}</b>\n"
        "<i>Профиль для AI-оценки</i>\n\n"
        f"🎯 <b>Роли:</b> {roles}\n"
        f"📍 <b>Город:</b> {escape(profile.location)}\n"
        f"💰 <b>Минимум:</b> {minimum_salary} ₽\n"
        f"🧩 <b>Сильные навыки:</b> {skills}\n\n"
        "<i>Профиль и резюме загружены и готовы к AI-анализу.</i>"
    )


def stats_text(stats: StatsResult) -> str:
    missing = (
        "\n".join(
            f"• {escape(skill)} <code>×{count}</code>"
            for skill, count in stats.common_missing_skills
        )
        or "Пока недостаточно данных"
    )
    interview_conversion = (
        stats.interviews / stats.applied * 100 if stats.applied else 0.0
    )
    offer_conversion = stats.offers / stats.applied * 100 if stats.applied else 0.0
    return (
        "<b>📊 Воронка поиска</b>\n\n"
        f"💼 Вакансий в базе: <b>{stats.total_vacancies}</b>\n"
        f"✨ Проанализировано: <b>{stats.analyzed}</b>\n"
        f"❤️ В избранном: <b>{stats.saved}</b>\n"
        f"📤 Откликов: <b>{stats.applied}</b>\n"
        f"🤝 Интервью: <b>{stats.interviews}</b>\n"
        f"🧪 Тестовых: <b>{stats.test_tasks}</b>\n"
        f"🎉 Офферов: <b>{stats.offers}</b>\n"
        f"🏁 Принято: <b>{stats.accepted_offers}</b>\n"
        f"⛔ Отказов: <b>{stats.rejected}</b>\n"
        f"📐 Отклик → интервью: <b>{interview_conversion:.1f}%</b>\n"
        f"📐 Отклик → оффер: <b>{offer_conversion:.1f}%</b>\n"
        f"📈 Среднее совпадение: <b>{stats.average_score:.1f}%</b>\n\n"
        f"<b>Чаще всего не хватает</b>\n{missing}"
    )


def search_progress_text(stage: str = "Подключаюсь к источникам…") -> str:
    return (
        "<b>🔎 Ищу подходящие вакансии</b>\n\n"
        f"{escape(stage)}\n\n"
        "<i>Результаты появятся здесь — новых сообщений не будет.</i>"
    )


def empty_collection_text(title: str, message: str) -> str:
    return (
        f"<b>📭 {escape(title)}</b>\n\n"
        f"{escape(message)}\n\n"
        "Можно запустить новый поиск или вернуться в главное меню."
    )


def hh_text(*, connected: bool, resumes: int, configured: bool) -> str:
    if connected:
        return (
            "<b>✅ HeadHunter подключён</b>\n\n"
            f"Синхронизировано резюме: <b>{resumes}</b>.\n"
            "Можно готовить и отправлять отклики из карточек вакансий."
        )
    if not configured:
        return (
            "<b>⚙️ HeadHunter не настроен</b>\n\n"
            "Добавьте параметры OAuth HeadHunter в конфигурацию приложения."
        )
    return (
        "<b>🔐 Подключение HeadHunter</b>\n\n"
        "Авторизация проходит на официальной странице HH. Логин и пароль "
        "не передаются боту."
    )


def application_loading_text() -> str:
    return (
        "<b>✍️ Готовлю отклик</b>\n\n"
        "Проверяю резюме и создаю персональное сопроводительное письмо…"
    )


def application_preview_text(preview: PreparedApplication) -> str:
    letter = _safe(preview.cover_letter, 2_700)
    next_step = (
        "\n\nℹ️ HeadHunter не разрешил боту прочитать список резюме. "
        "Письмо готово; резюме нужно будет выбрать на официальной странице HH."
        if preview.manual_submission_required
        else ""
    )
    return (
        "<b>✍️ Отклик готов</b>\n\n"
        f"<b>{escape(preview.vacancy_title)}</b>\n"
        f"🏢 {escape(preview.company)}\n"
        f"📄 {escape(preview.resume.title)}\n\n"
        f"<blockquote>{letter}</blockquote>\n"
        f"Проверьте письмо перед отправкой.{next_step}"
    )


def confirmation_text(preview: PreparedApplication) -> str:
    if preview.manual_submission_required:
        action = (
            "Автоматической отправки не будет: бот сохранит подготовленное письмо "
            "и откроет официальный ручной сценарий HeadHunter, где нужно выбрать "
            "резюме."
        )
    else:
        action = (
            "После подтверждения бот отправит отклик через официальный API HH. "
            "Если вакансия требует тест или внешнюю форму, откроется ручной сценарий."
        )
    return (
        "<b>🚀 Подтвердить отправку?</b>\n\n"
        f"<b>{escape(preview.vacancy_title)}</b>\n"
        f"🏢 {escape(preview.company)}\n"
        f"📄 {escape(preview.resume.title)}\n\n"
        f"{action}"
    )


def edit_letter_text() -> str:
    return (
        "<b>✏️ Редактирование письма</b>\n\n"
        "Отправьте новый полный текст одним сообщением. После сохранения ваше "
        "сообщение будет удалено из чата."
    )


def result_text(message: str, *, status: str, result_uncertain: bool = False) -> str:
    icon, title = {
        "submitted": ("✅", "Отклик отправлен"),
        "demo": ("🧪", "Демонстрация завершена безопасно"),
        "manual_action_required": ("↗️", "Требуется действие на HeadHunter"),
        "failed": ("⚠️", "Отправка не выполнена"),
    }.get(status, ("⚠️", "Результат отклика"))
    if result_uncertain:
        icon, title = "🔎", "Результат нужно проверить"
    draft_note = (
        "\n\n📝 <b>Черновик сохранён:</b> письмо и выбранное резюме не потеряны."
        if status in {"failed", "manual_action_required"}
        else ""
    )
    return f"<b>{icon} {title}</b>\n\n{escape(message)}{draft_note}"


def manual_application_confirmation_text(vacancy: Vacancy) -> str:
    return (
        "<b>✅ Отметить отклик?</b>\n\n"
        f"<b>{escape(vacancy.title)}</b>\n"
        f"🏢 {escape(vacancy.company)}\n\n"
        "Вы действительно уже отправили отклик на эту вакансию?\n\n"
        "После подтверждения она исчезнет из рекомендаций, а отклик "
        "сохранится в вашей истории."
    )


def manual_application_registered_text(vacancy: Vacancy) -> str:
    return (
        "<b>✅ Отклик зарегистрирован</b>\n\n"
        f"<b>{escape(vacancy.title)}</b>\n"
        f"🏢 {escape(vacancy.company)}\n\n"
        "Источник: <b>самостоятельный отклик</b>.\n"
        "Эта вакансия больше не появится в поиске и рекомендациях."
    )


def hidden_vacancy_text(vacancy: Vacancy) -> str:
    return (
        "<b>🙈 Вакансия скрыта</b>\n\n"
        f"<b>{escape(vacancy.title)}</b>\n"
        f"🏢 {escape(vacancy.company)}\n\n"
        "Она больше не появится в рекомендациях. Действие можно отменить сейчас."
    )


def lifecycle_text(vacancy: Vacancy, status: str) -> str:
    return (
        "<b>📌 Статус отклика</b>\n\n"
        f"<b>{escape(vacancy.title)}</b>\n"
        f"🏢 {escape(vacancy.company)}\n\n"
        f"Сейчас: <b>{escape(STATUS_NAMES.get(status, status))}</b>\n"
        "Выберите следующий этап — переход сохранится в истории."
    )
