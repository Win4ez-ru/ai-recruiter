from __future__ import annotations

from enum import StrEnum


class VacancyStatus(StrEnum):
    """Current position of a vacancy in the candidate's lifecycle."""

    NEW = "new"
    VIEWED = "viewed"
    SAVED = "saved"
    APPLIED_MANUAL = "applied_manual"
    APPLIED_BOT = "applied_bot"
    HIDDEN = "hidden"
    REJECTED = "rejected"
    INTERVIEW = "interview"
    TEST_TASK = "test_task"
    OFFER = "offer"
    OFFER_ACCEPTED = "offer_accepted"
    ARCHIVED = "archived"


class VacancyStatusSource(StrEnum):
    """Actor or integration which initiated a lifecycle transition."""

    USER = "user"
    MANUAL = "manual"
    BOT = "bot"
    SYSTEM = "system"
    EMPLOYER = "employer"
    IMPORT = "import"
    MIGRATION = "migration"


LEGACY_STATUS_MAP = {
    "applied": VacancyStatus.APPLIED_BOT,
    "skipped": VacancyStatus.HIDDEN,
}

EXCLUDED_FROM_RECOMMENDATIONS = frozenset(
    {
        VacancyStatus.APPLIED_MANUAL.value,
        VacancyStatus.APPLIED_BOT.value,
        VacancyStatus.HIDDEN.value,
        VacancyStatus.REJECTED.value,
        VacancyStatus.INTERVIEW.value,
        VacancyStatus.TEST_TASK.value,
        VacancyStatus.OFFER.value,
        VacancyStatus.OFFER_ACCEPTED.value,
        VacancyStatus.ARCHIVED.value,
    }
)

APPLICATION_RECORDED_STATUSES = frozenset(
    {
        VacancyStatus.APPLIED_MANUAL.value,
        VacancyStatus.APPLIED_BOT.value,
        VacancyStatus.INTERVIEW.value,
        VacancyStatus.TEST_TASK.value,
        VacancyStatus.OFFER.value,
        VacancyStatus.OFFER_ACCEPTED.value,
    }
)

MANUAL_APPLICATION_ELIGIBLE_STATUSES = frozenset(
    {
        VacancyStatus.NEW.value,
        VacancyStatus.VIEWED.value,
        VacancyStatus.SAVED.value,
    }
)

APPLIED_COLLECTION_STATUSES = frozenset(
    {
        *APPLICATION_RECORDED_STATUSES,
        VacancyStatus.REJECTED.value,
        VacancyStatus.ARCHIVED.value,
    }
)

_TRANSITIONS: dict[VacancyStatus, frozenset[VacancyStatus]] = {
    VacancyStatus.NEW: frozenset(
        {
            VacancyStatus.VIEWED,
            VacancyStatus.SAVED,
            VacancyStatus.APPLIED_MANUAL,
            VacancyStatus.APPLIED_BOT,
            VacancyStatus.HIDDEN,
            VacancyStatus.REJECTED,
            VacancyStatus.ARCHIVED,
        }
    ),
    VacancyStatus.VIEWED: frozenset(
        {
            VacancyStatus.SAVED,
            VacancyStatus.APPLIED_MANUAL,
            VacancyStatus.APPLIED_BOT,
            VacancyStatus.HIDDEN,
            VacancyStatus.REJECTED,
            VacancyStatus.ARCHIVED,
        }
    ),
    VacancyStatus.SAVED: frozenset(
        {
            VacancyStatus.VIEWED,
            VacancyStatus.APPLIED_MANUAL,
            VacancyStatus.APPLIED_BOT,
            VacancyStatus.HIDDEN,
            VacancyStatus.REJECTED,
            VacancyStatus.ARCHIVED,
        }
    ),
    VacancyStatus.APPLIED_MANUAL: frozenset(
        {
            VacancyStatus.INTERVIEW,
            VacancyStatus.TEST_TASK,
            VacancyStatus.REJECTED,
            VacancyStatus.OFFER,
            VacancyStatus.ARCHIVED,
        }
    ),
    VacancyStatus.APPLIED_BOT: frozenset(
        {
            VacancyStatus.INTERVIEW,
            VacancyStatus.TEST_TASK,
            VacancyStatus.REJECTED,
            VacancyStatus.OFFER,
            VacancyStatus.ARCHIVED,
        }
    ),
    VacancyStatus.INTERVIEW: frozenset(
        {
            VacancyStatus.TEST_TASK,
            VacancyStatus.REJECTED,
            VacancyStatus.OFFER,
            VacancyStatus.ARCHIVED,
        }
    ),
    VacancyStatus.TEST_TASK: frozenset(
        {
            VacancyStatus.INTERVIEW,
            VacancyStatus.REJECTED,
            VacancyStatus.OFFER,
            VacancyStatus.ARCHIVED,
        }
    ),
    VacancyStatus.OFFER: frozenset(
        {
            VacancyStatus.OFFER_ACCEPTED,
            VacancyStatus.REJECTED,
            VacancyStatus.ARCHIVED,
        }
    ),
    VacancyStatus.OFFER_ACCEPTED: frozenset({VacancyStatus.ARCHIVED}),
    VacancyStatus.HIDDEN: frozenset(
        {VacancyStatus.VIEWED, VacancyStatus.SAVED, VacancyStatus.ARCHIVED}
    ),
    VacancyStatus.REJECTED: frozenset({VacancyStatus.ARCHIVED}),
    VacancyStatus.ARCHIVED: frozenset({VacancyStatus.VIEWED}),
}


class VacancyStatusTransitionError(ValueError):
    pass


def normalize_status(value: VacancyStatus | str) -> VacancyStatus:
    if isinstance(value, VacancyStatus):
        return value
    legacy = LEGACY_STATUS_MAP.get(value)
    return legacy if legacy is not None else VacancyStatus(value)


def validate_transition(
    current: VacancyStatus | str,
    target: VacancyStatus | str,
) -> tuple[VacancyStatus, VacancyStatus]:
    current_status = normalize_status(current)
    target_status = normalize_status(target)
    if current_status == target_status:
        return current_status, target_status
    if target_status not in _TRANSITIONS[current_status]:
        raise VacancyStatusTransitionError(
            f"Unsupported vacancy transition: {current_status.value} -> "
            f"{target_status.value}"
        )
    return current_status, target_status


def allowed_transitions(status: VacancyStatus | str) -> frozenset[VacancyStatus]:
    return _TRANSITIONS[normalize_status(status)]


def has_registered_application(status: VacancyStatus | str) -> bool:
    return normalize_status(status).value in APPLICATION_RECORDED_STATUSES


def can_mark_applied_manual(status: VacancyStatus | str) -> bool:
    return normalize_status(status).value in MANUAL_APPLICATION_ELIGIBLE_STATUSES


def is_excluded_from_recommendations(status: VacancyStatus | str) -> bool:
    return normalize_status(status).value in EXCLUDED_FROM_RECOMMENDATIONS
