import json
from pathlib import Path

from pydantic import ValidationError

from app.schemas import CandidateProfile


class CandidateProfileError(RuntimeError):
    pass


def load_candidate_profile(path: Path) -> CandidateProfile:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise CandidateProfileError(f"Файл профиля не найден: {path}") from exc
    except OSError as exc:
        raise CandidateProfileError(f"Не удалось прочитать профиль: {path}") from exc
    try:
        return CandidateProfile.model_validate_json(raw)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise CandidateProfileError(f"Некорректный JSON профиля: {path}") from exc


def load_resume(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise CandidateProfileError(f"Файл резюме не найден: {path}") from exc
    except OSError as exc:
        raise CandidateProfileError(f"Не удалось прочитать резюме: {path}") from exc
    if not text:
        raise CandidateProfileError(f"Файл резюме пуст: {path}")
    return text
