# Демонстрация AI Recruiter работодателю

## Что показать за 7–10 минут

1. Dashboard: профиль готов, выбранный AI-провайдер, статус HH и число непросмотренных вакансий.
2. Поиск: дедупликация, cache и живой прогресс `HeadHunter 4/7` → `🤖 Анализирую лучшие варианты… 3/15`.
3. Top-5: убывающий score, причина рекомендации, сильные совпадения, пробелы, red flags и акцент для письма.
4. Избранное и отменяемое скрытие.
5. Подготовка персонального письма, редактирование, выбор резюме и двойное подтверждение.
6. Безопасный финал без реального POST благодаря `DEMO_MODE=true`.
7. Lifecycle и воронка: отклик → интервью → тестовое → оффер.
8. Репозиторий: архитектурная схема, CI, миграции и тесты.

## Безопасная конфигурация

Не используйте production-БД для показа. Создайте отдельный `.env` или временно задайте:

```env
DEMO_MODE=true
DATABASE_URL=sqlite+aiosqlite:///./demo_job_agent.db
DATABASE_AUTO_CREATE=false
```

Примените схему:

```bash
source .venv/bin/activate
alembic upgrade head
python run.py
```

`DEMO_MODE=true` не упрощает внутренний flow: бот всё равно получает вакансию, готовит письмо, создаёт одноразовое подтверждение и обрабатывает финальный callback. Блокируется только внешний `apply_to_vacancy`.

Перед screen sharing:

- закройте `.env`, БД и логи;
- отключите всплывающие уведомления;
- используйте обезличенные `data/*.example.*` или отдельный demo-профиль;
- убедитесь, что в Telegram нет личных сообщений на экране;
- не показывайте API keys, OAuth tokens и реальные контакты кандидата.

## YandexGPT

Если показываете облачный режим:

```env
AI_PROVIDER=yandex
YANDEX_API_KEY=...
YANDEX_FOLDER_ID=...
YANDEX_MODEL=yandexgpt-5.1
YANDEX_DATA_LOGGING_ENABLED=false
AI_RANKING_CONCURRENCY=3
```

Перед встречей выполните один реальный `/search`. Грант покрывает фактическое потребление, но не увеличивает квоты и контекст модели. Если ключ ещё не выдан или сеть нестабильна, переключитесь на уже проверенную локальную конфигурацию:

```env
AI_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:4b-instruct
```

## Preflight за пять минут

```bash
python -m compileall -q app tests
ruff check app tests migrations scripts run.py
ruff format --check app tests migrations scripts run.py
pytest -q
alembic current
alembic check
python -m pip_audit --local
python -m scripts.smoke_search --ai-limit 2
```

Команда использует встроенный синтетический профиль и временную БД. Она не читает
личное резюме, не запускает Telegram и не вызывает API отправки отклика.

Ожидаемый baseline: весь pytest проходит, coverage ≥65%, одна Alembic head revision, согласованные зависимости и отсутствие известных уязвимостей.

## Сильные тезисы для рассказа

- «Сначала дешёвый deterministic filter, затем ограниченное число LLM-вызовов».
- «Результат LLM не считается доверенным: JSON Schema и Pydantic проверяют его повторно».
- «Смена модели или промпта не оставляет старые оценки навсегда — есть input fingerprint».
- «Top-5 использует только текущие provider/model/prompt; legacy-оценка физически сохранена, но не может попасть в выдачу».
- «YandexGPT ранжирует с bounded concurrency, а финальная сортировка не зависит от порядка завершения запросов».
- «Неоднозначный POST не retry-ится: дубль отклика хуже ручной проверки».
- «При любой ошибке HH письмо и резюме остаются в durable draft; retry разрешён только когда известно, что это безопасно».
- «HH submission и локальный lifecycle фиксируются одной транзакцией, а startup reconciliation чинит crash window».
- «Telegram выглядит как приложение в одном сообщении, но состояние хранится в БД».
- «Для текущего масштаба модульный монолит проще и надёжнее искусственных микросервисов».

## Поминутный сценарий

1. **0:00–1:00 — запуск и dashboard.** Покажите JSON startup logs, YandexGPT, статус HH и явный режим реальных/демо-откликов.
2. **1:00–2:30 — поиск.** Запустите `/search`, поясните дедупликацию, rule-based prefilter, cache и bounded concurrency.
3. **2:30–4:00 — Top-5.** Пролистайте несколько карточек, откройте `📋 Подробнее`, покажите причины, блокеры и red flags.
4. **4:00–5:00 — UX.** Добавьте вакансию в избранное, скройте другую и отмените скрытие.
5. **5:00–7:00 — отклик.** Подготовьте письмо, измените его, выберите резюме и дойдите до второго подтверждения. На интервью используйте `DEMO_MODE=true`.
6. **7:00–8:00 — lifecycle.** Покажите ручной отклик, интервью/тестовое/оффер и аналитику конверсии.
7. **8:00–10:00 — код.** Откройте `docs/ARCHITECTURE.md`, `VacancySearchService`, атомарную `finalize_submitted`, миграции и CI.

Не демонстрируйте реальный error POST в живом HH. Гарантию сохранения письма показывайте тестами `test_retry_reuses_edited_cover_letter_and_selected_resume` и `test_unknown_submission_result_never_offers_automatic_retry`.

## Что честно назвать следующим этапом

- application-level encryption OAuth-токенов через KMS/secret manager;
- golden eval-набор из размеченных вакансий для сравнения моделей;
- редактирование уже настраиваемой search policy прямо из Telegram-профиля;
- tenant-aware schema и webhook/worker topology только при переходе к нескольким пользователям.
