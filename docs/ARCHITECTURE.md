# Архитектура AI Recruiter

## Контекст и цель

AI Recruiter — однопользовательский асинхронный модульный монолит. Его главная ценность не в количестве интеграций, а в надёжном сквозном сценарии: найти вакансию, объяснить соответствие профилю, подготовить контролируемый отклик и вести результат по воронке найма.

Микросервисы, Redis и очередь намеренно не добавлены: для одного пользователя они увеличили бы эксплуатационную сложность без продуктовой пользы. Границы модулей оставляют возможность вынести workers и multi-tenant API позже.

## Компоненты

```mermaid
flowchart LR
    TG[Telegram user] --> BOT[aiogram routers]
    BOT --> UI[UIManager\ndurable screen state]
    BOT --> SEARCH[VacancySearchService]
    BOT --> APPLY[HHApplicationService]

    SEARCH --> HH[HHClient\nofficial API]
    SEARCH --> FILTER[VacancyFilter\nrule based]
    SEARCH --> RANK[VacancyRanker]
    RANK --> AI{AIProvider}
    AI --> YA[YandexGPT\nChat Completions]
    AI --> OL[Ollama\n/api/chat]
    AI --> OA[OpenAI\nResponses API]

    APPLY --> HH
    APPLY --> LETTER[CoverLetterService]
    LETTER --> AI

    SEARCH --> REPOS[Repositories]
    APPLY --> REPOS
    UI --> REPOS
    REPOS --> DB[(SQLite / PostgreSQL)]

    SCHED[APScheduler] --> SEARCH
    HTTP[HTTP server] --> OAUTH[HH OAuth callback]
    HTTP --> HEALTH[Live / Ready]
```

## Основной search flow

1. Семь запросов к HH объединяются по `external_id`.
2. Детали новых и устаревших вакансий обновляются с ограниченной конкурентностью.
3. Дешёвый фильтр исключает нерелевантные и заведомо блокирующие позиции.
4. AI получает только кандидатов после фильтра и возвращает строгую `VacancyAnalysisResult`.
5. Pydantic повторно проверяет диапазоны, enum, массивы и запрет лишних полей.
6. В анализе сохраняются provider, model, prompt version и SHA-256 входных данных.
7. Изменение вакансии, профиля, резюме, модели или промпта делает оценку устаревшей и запускает переанализ.
8. `MAX_AI_ANALYSES_PER_SEARCH` ограничивает стоимость и длительность одного запуска.

## AI-провайдеры

| Провайдер | Транспорт | Structured output | Типичный сценарий |
|---|---|---|---|
| YandexGPT | OpenAI-compatible Chat Completions | `json_schema` + Pydantic | Основной облачный режим с грантом |
| Ollama | Native `/api/chat` | JSON Schema + Pydantic | Бесплатная локальная разработка и fallback |
| OpenAI | Responses API | SDK parse + Pydantic | Опциональный облачный режим |

Бизнес-сервисы зависят только от протокола `AIProvider`. Названия провайдеров не попадают в пользовательские ошибки; конкретная модель видна только на dashboard и в технических метаданных.

Для Yandex по умолчанию отправляется `x-data-logging-enabled: false`, поскольку prompt содержит профиль и резюме. API-ключ и folder ID проходят только через environment/secret manager.

## Инварианты надёжности

- Один внешний HH-отклик имеет уникальную identity: пользователь + вакансия + резюме.
- Первое подтверждение создаёт хешированный одноразовый token; только второе получает submission lease.
- Неоднозначный timeout внешнего POST не повторяется автоматически.
- Успешный HH-результат, lifecycle вакансии и запись истории фиксируются одной локальной транзакцией.
- При старте `submitted` без lifecycle автоматически восстанавливаются; старые `submitting` переводятся в `submission_result_unknown`.
- Только реально отрисованная карточка получает `is_sent=true`; загрузка подборки не уничтожает непросмотренные элементы.
- Долгая AI-операция имеет generation token и не может перерисовать экран после Back или нового действия.
- Активный экран, collection kind, позиция и pending OAuth intent переживают рестарт.
- Telegram failover повторяет только безопасные методы; потенциально дублирующие отправки вслепую не повторяются.

## Lifecycle вакансии

```mermaid
stateDiagram-v2
    [*] --> new
    new --> viewed
    viewed --> saved
    saved --> viewed
    new --> hidden
    viewed --> hidden
    saved --> hidden
    hidden --> viewed
    hidden --> saved
    viewed --> applied_manual
    viewed --> applied_bot
    saved --> applied_manual
    saved --> applied_bot
    applied_manual --> interview
    applied_bot --> interview
    applied_manual --> test_task
    applied_bot --> test_task
    interview --> test_task
    test_task --> interview
    interview --> offer
    test_task --> offer
    offer --> offer_accepted
    applied_manual --> rejected
    applied_bot --> rejected
    interview --> rejected
    test_task --> rejected
    offer --> rejected
    offer_accepted --> archived
```

Текущее состояние хранится в `applications`, а каждый переход — в append-only `vacancy_status_history`. `application_source` не теряется при переходе к интервью, отказу или архиву, поэтому воронка остаётся корректной.

## Данные и миграции

- SQLAlchemy 2 async repositories скрывают транзакции от transport/UI слоёв.
- SQLite — локальный single-process режим; PostgreSQL проверяется отдельным CI job.
- Alembic имеет одну линейную head revision и тестируется upgrade/check/downgrade на SQLite.
- Legacy-база без `alembic_version` принимается только после точного сравнения metadata командой `python -m scripts.adopt_legacy_database`; слепой `stamp` запрещён runbook.
- Реальные резюме и профиль не копируются в Docker image; контейнер получает их через read-only volume или secret files.

## Наблюдаемость и качество

- JSON logs редактируют credentials и структурированные sensitive fields.
- `/health/live` отражает жизнь event loop; `/health/ready` становится `200` только после БД и Telegram bootstrap.
- CI: Python 3.11–3.13, Linux/macOS/Windows, Ruff, format check, 142 теста, coverage gate 65%, pip-audit, SQLite/PostgreSQL migrations и Docker build.

## Осознанные ограничения

- Один `TELEGRAM_USER_ID`, одна polling-реплика и один профиль.
- OAuth-токены HH пока требуют защиты диска/БД и не имеют application-level KMS encryption.
- Rule-based policy и поисковые запросы пока ориентированы на iOS-профиль.
- Нет golden eval-набора для сравнения качества YandexGPT и Ollama на размеченных вакансиях.

Следующий разумный шаг — не микросервисы, а versioned eval dataset, шифрование OAuth-токенов, настройка search policy из профиля и tenant-aware schema при появлении реальных пользователей.
