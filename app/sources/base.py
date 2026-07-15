from abc import ABC, abstractmethod
from typing import Any


class VacancySource(ABC):
    @abstractmethod
    async def search_vacancies(
        self, query: str, *, max_results: int = 100
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def get_vacancy(self, vacancy_id: str) -> dict[str, Any]:
        raise NotImplementedError
