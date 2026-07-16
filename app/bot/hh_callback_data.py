from aiogram.filters.callback_data import CallbackData


class VacancyCallback(CallbackData, prefix="vac"):
    action: str
    vacancy_id: int


class PrepareApplicationCallback(CallbackData, prefix="hhp"):
    vacancy_id: int


class DraftApplicationCallback(CallbackData, prefix="hhd"):
    action: str
    application_id: int


class ResumeCallback(CallbackData, prefix="hhr"):
    vacancy_id: int
    resume_id: int


class ConfirmationCallback(CallbackData, prefix="hhc"):
    token: str


class HHOAuthCallback(CallbackData, prefix="hho"):
    action: str
