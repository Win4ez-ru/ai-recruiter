# AI Recruiter — персональный Telegram-бот для поиска iOS-вакансий

[![CI](https://github.com/Win4ez-ru/AI_recruter/actions/workflows/ci.yml/badge.svg)](https://github.com/Win4ez-ru/AI_recruter/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)

AI Recruiter получает свежие вакансии через официальный API HeadHunter, удаляет дубликаты, отсеивает нерелевантные позиции без затрат на LLM, формирует объяснимый Top-5 и ведёт отклик до интервью и оффера в приватном Telegram-интерфейсе. AI-слой поддерживает YandexGPT, локальную Ollama и OpenAI. Проект остаётся уместным модульным монолитом для одного пользователя, но включает production-oriented контур: bounded concurrency, cache fingerprinting, retry/backoff, идемпотентность, health checks, graceful shutdown, Alembic, PostgreSQL, Docker и cross-platform CI.

Документы: [архитектура](docs/ARCHITECTURE.md) · [сценарий демонстрации](docs/DEMO.md) · [deployment runbook](docs/DEPLOYMENT.md).

## Коротко: как начать пользоваться

1. Создайте бота через [@BotFather](https://t.me/BotFather) и сохраните токен.
2. Узнайте свой числовой Telegram ID по инструкции ниже.
3. Установите Python 3.11+, скопируйте `.env.example` в `.env`, заполните Telegram-настройки и выберите YandexGPT, Ollama либо OpenAI.
4. Установите зависимости командой `pip install -r requirements-dev.txt` для разработки или `pip install -r requirements.txt` для runtime.
5. Запустите `python run.py`, откройте созданного бота в Telegram и отправьте `/start`.
6. Пока нужен бот, процесс `python run.py` должен продолжать работать. Для режима 24/7 разместите его на VPS или домашнем сервере.

Telegram-бот не нужно отдельно «загружать» в Telegram: BotFather создает его учетную запись, а запущенная Python-программа подключается к этой учетной записи по токену и получает команды через polling.

## Проект для портфолио

Пример формулировки для резюме:

> Разработал асинхронного Telegram-агента для поиска iOS-вакансий: интеграция с HeadHunter API, rule-based фильтрация, LLM-ранжирование через Structured Outputs, персональные сопроводительные письма, SQLite/SQLAlchemy, APScheduler и защита от повторной обработки.

На собеседовании можно отдельно рассказать про устойчивость внешних API, дедупликацию, provider-neutral AI-контракт, строгую Pydantic-валидацию, автоматическую переоценку при смене модели, атомарную финализацию отклика и восстановление незавершённых операций после сбоя.

## Возможности

- настраиваемая search policy; по умолчанию семь запросов для iOS, Swift, SwiftUI и стажировок;
- настраиваемые регион, глубина поиска и remote-фильтр; по умолчанию Санкт-Петербург и последние 7 дней;
- пагинация, retry/backoff и таймауты для HH API;
- автоматический Telegram failover между direct, HTTP и SOCKS proxy;
- отдельные proxy и timeout-политики для Telegram, HH и каждого AI-провайдера;
- обработка `Retry-After`, rate limit, `5xx` и transport timeout;
- JSON-логи в stdout с редактированием секретов;
- liveness/readiness endpoints и graceful shutdown по `SIGINT`/`SIGTERM`;
- SQLite для локального режима, PostgreSQL/asyncpg и Alembic для deployment;
- дедупликация по `source + external_id`;
- предварительный фильтр без LLM;
- структурированный LLM-анализ с оценкой 0–100 через YandexGPT, OpenAI или локальную Ollama;
- ограниченный параллелизм облачного ranking (`3` по умолчанию) и последовательный безопасный профиль для Ollama;
- traceability оценки: provider, модель, версия промпта и hash входных данных;
- обновление изменившихся вакансий по TTL и переоценка при смене входов;
- Top-5 только из актуальных оценок текущих provider/model/prompt, без примеси legacy-результатов;
- индивидуальное сопроводительное письмо;
- подключение аккаунта HeadHunter через OAuth 2 Authorization Code + PKCE;
- синхронизация и выбор резюме HeadHunter;
- черновик отклика, редактирование письма и два отдельных подтверждения;
- сохранение письма и выбранного резюме при OAuth, rate limit, `5xx` и сетевых ошибках;
- безопасное возобновление черновика; неизвестный результат POST нельзя повторить вслепую;
- единый app-like интерфейс: все разделы редактируют одно активное сообщение;
- карточная навигация по вакансиям без вертикальных дайджестов;
- сохранение активного UI-сообщения и позиции в коллекции между перезапусками;
- одноразовые подтверждения, защита от конкурентной обработки и периодическое восстановление зависших submission lease;
- единый жизненный цикл вакансии: `new`, `viewed`, `saved`, `applied_manual`, `applied_bot`, `hidden`, `rejected`, `interview`, `test_task`, `offer`, `offer_accepted`, `archived`;
- дата, источник отклика и неизменяемая история всех переходов статуса;
- управление этапами `интервью → тестовое → оффер → принят` из Telegram;
- безопасный `DEMO_MODE`, который проходит весь сценарий без внешней отправки;
- SQLite и асинхронный SQLAlchemy 2;
- ручной поиск и автоматический запуск по расписанию;
- защита от повторного анализа и повторной отправки;
- приватный доступ только для заданного Telegram User ID.

## Архитектура

Приложение представляет собой один асинхронный composition root с разделенными transport, repository и service слоями:

1. `HHClient` делает запросы только к официальному `api.hh.ru` и объединяет выдачу по настроенным региональному и remote-фильтрам.
2. `VacancySearchService` дедуплицирует результаты, получает полные карточки и сохраняет новые вакансии.
3. `VacancyFilter` исключает очевидно неподходящие позиции обычными правилами Python.
4. `VacancyRanker` вызывает общий `AIProvider`: YandexGPT использует OpenAI-compatible Chat Completions, OpenAI — Responses API, Ollama — локальный `/api/chat`; итог всегда проверяет одна Pydantic-схема. Облачные запросы исполняются с ограниченным concurrency, а итоговый порядок определяется только после сохранения всех результатов.
5. Lifecycle-репозиторий атомарно сохраняет текущий статус, источник и запись в истории переходов.
6. `UIManager` хранит активный экран и коллекцию карточек, редактирует одно Telegram-сообщение и восстанавливает его после рестарта.
7. `DigestService` обновляет единую карточную коллекцию, а APScheduler запускает тот же workflow по расписанию.
8. `HHOAuthService` проверяет одноразовый `state`, обменивает OAuth-код с PKCE и обновляет токены.
9. `HHApplicationService` готовит черновик, а локальная финализация HH-результата и lifecycle вакансии происходит в одной транзакции с startup reconciliation.
10. `FailoverTelegramSession` изолирует сетевые маршруты Telegram, а `HHClient` и AI-провайдеры используют собственные HTTPX transports.
11. `HealthRegistry` и общий HTTP-сервер обслуживают OAuth callback, liveness и readiness.

Сбой одной вакансии или внешнего API логируется и не останавливает процесс. Неоднозначные non-idempotent операции не повторяются вслепую, чтобы не создавать дубликаты или не повреждать OAuth-состояние.

## Требования

- Python 3.11 или новее;
- Telegram-бот и его токен;
- сервисный аккаунт Yandex Cloud с API-ключом, OpenAI API key либо установленная локальная Ollama;
- интернет-доступ к выбранному облачному AI, `api.hh.ru` и Telegram Bot API.

## Создание Telegram-бота

1. Откройте официальный чат [@BotFather](https://t.me/BotFather).
2. Выполните `/newbot`.
3. Задайте имя и username, заканчивающийся на `bot`.
4. Скопируйте выданный токен в `TELEGRAM_BOT_TOKEN`.
5. Не публикуйте токен и не добавляйте файл `.env` в Git.

## Получение Telegram User ID

Напишите созданному боту любое сообщение. Затем откройте в браузере:

~~~text
https://api.telegram.org/bot<ВАШ_ТОКЕН>/getUpdates
~~~

В ответе найдите `message.from.id` и запишите число в `TELEGRAM_USER_ID`. После первого запуска бот отвечает только этому ID. Чужим пользователям он сообщает, что является приватным.

## Установка

Клонируйте репозиторий и перейдите в его каталог:

~~~bash
git clone https://github.com/Win4ez-ru/AI_recruter.git
cd AI_recruter
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
~~~

Windows PowerShell:

~~~powershell
git clone https://github.com/Win4ez-ru/AI_recruter.git
cd AI_recruter
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
~~~

Для Windows `cmd.exe` используйте `.venv\Scripts\activate.bat`.

## Настройка `.env`

Скопируйте пример:

~~~bash
cp .env.example .env
~~~

Windows PowerShell:

~~~powershell
Copy-Item .env.example .env
~~~

Заполните:

~~~env
TELEGRAM_BOT_TOKEN=токен_от_BotFather
TELEGRAM_USER_ID=ваш_числовой_id
AI_PROVIDER=yandex
YANDEX_API_KEY=ваш_api_ключ
YANDEX_FOLDER_ID=идентификатор_каталога
YANDEX_MODEL=yandexgpt-5.1
YANDEX_DATA_LOGGING_ENABLED=false
DATABASE_URL=sqlite+aiosqlite:///./job_agent.db
DATABASE_AUTO_CREATE=true
HH_USER_AGENT=AIRecruiter/1.0 (your-email@example.com)
HH_CLIENT_ID=
HH_CLIENT_SECRET=
HH_REDIRECT_URI=http://127.0.0.1:8080/oauth/hh/callback
HH_SEARCH_QUERIES=iOS Developer,iOS-разработчик,Junior iOS Developer,Swift Developer
HH_SEARCH_AREA_ID=2
HH_SEARCH_PERIOD_DAYS=7
HH_SEARCH_REMOTE=true
HTTP_HOST=127.0.0.1
HTTP_PORT=8080
SEARCH_INTERVAL_HOURS=12
MIN_SCORE_TO_SEND=65
MAX_VACANCIES_PER_DIGEST=5
MAX_AI_ANALYSES_PER_SEARCH=25
AI_RANKING_CONCURRENCY=3
DEMO_MODE=false
LOG_FORMAT=json
LOG_FILE_ENABLED=false
~~~

Полный список timeout, retry, proxy, health, pool и logging параметров находится в `.env.example`. Ключ требуется только выбранному облачному провайдеру; для HH рекомендуется указывать название приложения и контактный email в User-Agent.

## YandexGPT через грант Yandex Cloud

Для долгоживущего бота используйте сервисный аккаунт с ролью `ai.languageModels.user` и создайте API-ключ через интерфейс AI Studio по актуальной инструкции Yandex. Не фиксируйте вручную один scope в IaC без сверки документации: разные страницы Yandex сейчас называют `yc.ai.languageModels.execute` и `yc.ai.foundationModels.execute`. Грант привязывается к платёжному аккаунту и автоматически уменьшает фактическую стоимость; отдельного «ключа гранта» в приложении нет.

~~~env
AI_PROVIDER=yandex
YANDEX_API_KEY=
YANDEX_FOLDER_ID=
YANDEX_MODEL=yandexgpt-5.1
YANDEX_BASE_URL=https://ai.api.cloud.yandex.net/v1
YANDEX_TIMEOUT_SECONDS=120
YANDEX_MAX_RETRIES=3
YANDEX_DATA_LOGGING_ENABLED=false
AI_RANKING_CONCURRENCY=3
~~~

Фабрика собирает URI `gpt://<folder-id>/yandexgpt-5.1`. Ранжирование идёт через Chat Completions с `response_format=json_schema`, после чего ответ повторно валидируется Pydantic. До трёх вакансий анализируются параллельно; malformed-ответ одной вакансии не отменяет остальные, а provider-level сбой прекращает запуск новых запросов. Заголовок `x-data-logging-enabled: false` включён по умолчанию, поскольку prompt содержит профиль и резюме. Официальные инструкции: [API-ключ](https://aistudio.yandex.ru/docs/ru/ai-studio/operations/get-api-key.html), [Structured Output](https://aistudio.yandex.ru/docs/ru/ai-studio/operations/generation/completions-structured.html), [отключение логирования](https://aistudio.yandex.ru/docs/ru/ai-studio/operations/disable-logging.html).

## Локальная Ollama без оплаты API-токенов

На MacBook Air M2 с 8 ГБ памяти рекомендуется компактная `qwen3:4b-instruct`. Установите Ollama, запустите локальный сервер и один раз загрузите модель:

~~~bash
ollama serve
ollama pull qwen3:4b-instruct
~~~

Если приложение Ollama уже запущено в macOS, отдельный `ollama serve` не нужен. В `.env` выберите локальный провайдер:

~~~env
AI_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:4b-instruct
OLLAMA_TIMEOUT_SECONDS=180
OLLAMA_MAX_RETRIES=2
OLLAMA_CONTEXT_LENGTH=16384
~~~

В этом режиме `OPENAI_API_KEY` и `OPENAI_MODEL` могут быть пустыми. Ранжирование выполняется через JSON Schema и проверяется существующей Pydantic-моделью; сопроводительные письма также генерируются локально. Для запуска приложения в Docker Compose адрес Ollama автоматически меняется на `http://host.docker.internal:11434`.

## Настройка OAuth HeadHunter

1. Зарегистрируйте приложение в [кабинете разработчика HeadHunter](https://dev.hh.ru/admin) и получите `client_id` и защищенный ключ.
2. Укажите callback URL приложения. Он должен совпадать с `HH_REDIRECT_URI`.
3. Заполните `HH_CLIENT_ID`, `HH_CLIENT_SECRET` и `HH_REDIRECT_URI` в `.env`.
4. Настройте адрес приема callback через `HTTP_HOST` и `HTTP_PORT`.
5. Запустите бота, выполните `/hh` и нажмите «Подключить HeadHunter».

Если в кабинете HH уже зарегистрирован `http://localhost:8080/callback`, менять его не нужно: оставьте это же значение в `HH_REDIRECT_URI`. Для VPS используйте публичный HTTPS URL и направьте reverse proxy на `HTTP_HOST:HTTP_PORT`. Redirect URI должен совпадать с настройкой HH полностью, включая host и path.

OAuth использует уникальный `state` со сроком жизни 10 минут и PKCE S256. Хеш `state` привязан к `TELEGRAM_USER_ID` и становится недействительным при первом callback. Access/refresh-токены не передаются в Telegram и не выводятся в логи.

Токены HH хранятся в базе без application-level encryption at rest. Ограничьте доступ к базе и резервным копиям; перед multi-tenant SaaS необходимо добавить envelope encryption с ключом из KMS/secret manager.

## Профиль и резюме

Обезличенные шаблоны находятся в `data/candidate_profile.example.json` и `data/resume.example.txt`. Рабочие файлы называются `data/candidate_profile.json` и `data/resume.txt`. JSON должен оставаться валидным и соответствовать существующей структуре.

Production Docker image намеренно не содержит рабочие профиль и резюме: передавайте каталог `data/` read-only volume или через секретное файловое хранилище платформы. Облачный AI получает содержимое этих файлов для анализа; выбирайте провайдера и настройки хранения данных осознанно.

После изменения файлов перезапустите процесс: профиль загружается при старте.

## Запуск

~~~bash
python run.py
~~~

При локальном `DATABASE_AUTO_CREATE=true` таблицы создаются автоматически. В production сначала выполните `alembic upgrade head` и установите `DATABASE_AUTO_CREATE=false`. По умолчанию логи выводятся как JSON в stdout; файловая ротация включается отдельно.

Для показа работодателю включите `DEMO_MODE=true`: поиск, AI-анализ, письмо и двойное подтверждение работают полностью, но финальный POST отклика в HH не выполняется. Готовый маршрут показа описан в [docs/DEMO.md](docs/DEMO.md).

Docker Compose:

~~~bash
docker compose up --build -d
docker compose logs -f job-agent
~~~

Подробные сценарии запуска на VPS, Railway, Render и cloud-платформах описаны в [deployment runbook](docs/DEPLOYMENT.md).

## Интерфейс Telegram

Основной способ навигации — компактное inline-меню. Поиск, коллекции, карточка вакансии, профиль, статистика, OAuth и подготовка отклика сменяют друг друга внутри одного сообщения. Пользовательские команды и введенный текст письма удаляются после обработки, поэтому чат не накапливает технические сообщения.

Основной результат поиска — до пяти лучших актуальных вакансий по убыванию score. Они показываются по одной с навигацией `◀ Назад · 3 / 5 · Вперёд ▶`, кратким и подробным режимами, быстрым сохранением, отменяемым скрытием и переходом к отклику. Если порог качества прошли только две вакансии, интерфейс честно покажет Top-2, а не добавит слабые варианты. Непросмотренной перестаёт считаться только реально открытая карточка, а не вся подборка. Кнопка `✅ Я уже откликнулся` позволяет подтвердить самостоятельный отклик на HH, LinkedIn, Авито или другом сервисе. После отклика этапы интервью, тестового, отказа и оффера меняются прямо из карточки.

Команды остаются как быстрые deeplink-входы:

- `/start` — главное меню;
- `/help` — справка;
- `/search` — поиск, анализ и открытие карточной коллекции;
- `/new` — еще не отправленные вакансии с оценкой выше порога;
- `/top` — Top-5 актуальных оценок, включая ранее показанные;
- `/saved` — вакансии со статусом `saved`;
- `/applied` — самостоятельные и отправленные ботом отклики, включая последующие этапы;
- `/stats` — воронка откликов, интервью, тестовых и офферов;
- `/profile` — краткий профиль, используемый для AI-анализа;
- `/hh` — состояние подключения или запуск OAuth HeadHunter.

Кнопка `✍️ Подготовить отклик` открывает сценарий в том же сообщении. Бот проверяет OAuth, синхронизирует резюме, предлагает выбор, генерирует или использует сохраненное письмо и показывает полный предварительный просмотр. Если защитный шлюз HH отклоняет чтение собственных резюме с общим `403 forbidden`, бот использует `HH_DEFAULT_RESUME_ID`. Если резервный идентификатор не задан, выбор резюме переносится на официальную страницу HeadHunter. Письмо можно изменить через FSM; введенное пользователем сообщение удаляется после сохранения. Первое подтверждение только создаёт одноразовый токен, обработка начинается после `🚀 Да, отправить на HH`.

### Отправка через официальный API HH

Для обычной вакансии после двух подтверждений бот отправляет отклик официальным методом `POST /negotiations/response` с OAuth-токеном соискателя, выбранным резюме и проверенным текстом письма. При успешном `201 Created` локальные статусы меняются на `submitted` и `applied_bot`. Ошибка `already_applied` считается успешной синхронизацией, сохраняется как внешний самостоятельный отклик и не вызывает повторный запрос.

Текущий lifecycle хранится в одной колонке `applications.status`, без отдельных `is_saved`/`is_hidden`/`is_applied`. `application_source` фиксирует `manual` или `bot`, `applied_at` — дату отклика, а `vacancy_status_history` сохраняет каждый переход с инициатором, причиной и расширяемыми JSON-деталями. Поиск и дайджест исключают ручные и бот-отклики, скрытые, отклонённые и все последующие этапы процесса найма.

Для обязательного теста, внешней формы, архивной вакансии или запрета со стороны HH используется `manual_action_required` и официальный URL. Временные `429`/`5xx` и connect-сбои сохраняются как recoverable `failed`: кнопка `🔄 Повторить отправку` использует то же письмо и резюме, но создаёт новое подтверждение. При истёкшем OAuth предлагается переподключить HH и продолжить сохранённый черновик. Неоднозначный read/write timeout POST не повторяется: сервер мог принять отклик до разрыва соединения, поэтому доступны только проверка сайта и ручная отметка `✅ Я откликнулся на HH`.

## Тесты

~~~bash
python -m pytest
~~~

Локальный regression-набор держит coverage gate не ниже 65%. Внешние API заменены моками. Проверяются контракты OpenAI/Ollama/Yandex, приватность Yandex-запроса, HH-клиент, фильтрация, Top-5 и порядок ranking, bounded concurrency, cache scope, карточная навигация, сохранение непросмотренных вакансий, OAuth recovery, сохранность отредактированного письма, duplicate callbacks, lifecycle, атомарная финализация, crash reconciliation, миграции и безопасный demo flow.

~~~bash
pytest --cov=app --cov-report=term-missing --cov-fail-under=65
ruff check app tests migrations scripts run.py
ruff format --check app tests migrations scripts run.py
python -m pip_audit --local
~~~

Дополнительно проверяются OAuth/PKCE, refresh-токены, получение резюме, официальный отклик, черновики, FSM-редактирование, два подтверждения, просроченный и чужой токен, последовательная и конкурентная идемпотентность, timeout/rate limit и ручной fallback.

Безопасный real-network smoke test использует временную БД, синтетический профиль без персональных данных, один HH-запрос и не более двух AI-вызовов. Локальные файлы профиля и резюме не читаются; Telegram и отправка отклика в этот скрипт не входят:

~~~bash
python -m scripts.smoke_search --ai-limit 2
~~~

## Структура проекта

~~~text
AI_recruter/
├── app/
│   ├── bot/              # handlers, callbacks, карточки и клавиатуры
│   ├── network/          # retry и Telegram transport failover
│   ├── repositories/     # SQLAlchemy persistence
│   ├── scheduler/        # периодический запуск поиска
│   ├── services/         # фильтр, ranker, письма, search workflow, digest
│   ├── sources/          # официальный HH API
│   ├── config.py         # pydantic-settings
│   ├── database.py       # async engine/session factory
│   ├── health.py         # lifecycle и passive health state
│   ├── models.py         # SQLAlchemy-модели
│   ├── schemas.py        # Pydantic-схемы
│   └── main.py           # сборка зависимостей и lifecycle
├── data/
│   ├── candidate_profile.example.json
│   └── resume.example.txt
├── tests/
├── migrations/           # Alembic revisions
├── docs/                 # architecture, demo и deployment runbook
├── scripts/              # безопасные maintenance-команды
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── requirements.txt
├── requirements-dev.txt
└── run.py
~~~

## Возможные проблемы

### Ошибка конфигурации

Приложение выводит список некорректных полей и завершается. Проверьте, что `.env` лежит рядом с `run.py`, значения не пусты, а `TELEGRAM_USER_ID` — число.

### HeadHunter возвращает 400, 403, 429 или captcha

Проверьте `HH_USER_AGENT` и добавьте контактный email. Клиент повторяет `429`, временные `5xx` и безопасные GET-запросы после transport timeout. Публичная выдача HH может вводить CAPTCHA или отклонять конкретный IP; приложение не обходит эти ограничения. Для разрешенного отдельного маршрута используйте `HH_PROXY_URL`.

Для поиска бот получает отдельный application-токен по `client_credentials`, кеширует его в памяти процесса и не выводит в лог. Поэтому `HH_CLIENT_ID` и `HH_CLIENT_SECRET` нужны не только для подключения аккаунта через `/hh`, но и для стабильного доступа к `/vacancies`. Если эти параметры не заполнены, остается публичный запрос, для которого HH может потребовать CAPTCHA.

Сообщение `HeadHunter не принял авторизацию приложения` означает, что HH не нашел указанную пару `client_id`/`client_secret`. Скопируйте оба значения из одной и той же карточки приложения в кабинете разработчика; если защищенный ключ был заменен, обновите `HH_CLIENT_SECRET` и перезапустите процесс.

Для OAuth-методов бот нормализует истекшую или отозванную авторизацию, недоступное резюме, rate limit и временные ошибки без показа сырого JSON, HTML или traceback. CAPTCHA не обходится; используется официальный ручной путь, когда он доступен.

### AI-модель недоступна

Проверьте выбранный `AI_PROVIDER`, ключ/модель облачного провайдера либо статус `ollama serve`. Каждый провайдер имеет отдельные timeout/retry/proxy настройки. Provider-level сбой останавливает дальнейшие AI-вызовы текущего поиска, но не завершает процесс; необработанные вакансии будут рассмотрены позднее.

### Telegram не принимает сообщение

Убедитесь, что пользователь первым написал боту и не заблокировал его. При сетевой блокировке задайте `TELEGRAM_PROXY_URLS`; несколько URL образуют failover-цепочку. Системный VPN работает без изменения кода. Если все маршруты недоступны, процесс остается жив и продолжает backoff, но рабочий внешний маршрут все равно необходим.

Для direct- и proxy-маршрутов используется проверяемый CA bundle; TLS-проверка сертификатов не отключается.

### База заблокирована

Не запускайте несколько копий приложения с одним SQLite-файлом. Остановите лишний процесс и проверьте права записи в каталог проекта.

## Границы текущей версии

- один пользователь и один профиль;
- SQLite подходит только для одной реплики; PostgreSQL и Alembic поддерживаются для deployment;
- только официальный HeadHunter API;
- токены OAuth хранятся в базе без application-level шифрования;
- вакансии с обязательным тестом или внешней формой требуют ручного завершения;
- качество и скорость ранжирования и писем зависят от выбранной YandexGPT/OpenAI/Ollama-модели и локального оборудования;
- простой rule-based фильтр может давать пограничные ложные срабатывания;
- нет веб-интерфейса, многопользовательского режима и горизонтального масштабирования;
- scheduler, search lock и polling живут в одном процессе;
- OAuth-токены еще не зашифрованы application-level ключом.

## Следующий архитектурный этап

- Хабр Карьера и вакансии из Telegram-каналов;
- несколько версий резюме и адаптация под вакансию;
- анализ входящих email-ответов и обновление статусов;
- статистика конверсии и востребованных навыков;
- вопросы для собеседования;
- веб-интерфейс;
- tenant-aware PostgreSQL schema и управление пользователями;
- Telegram webhook ingress, очередь workers и Redis/distributed locks;
- KMS-шифрование OAuth-токенов и audit log.

## Безопасность и правила продукта

Секреты читаются только из окружения. Бот не хранит логин или пароль HeadHunter, не обходит ограничения площадки, не собирает скрытые данные HR и не использует браузерную автоматизацию. Официальный API-отклик не выполняется без двух явных подтверждений пользователя.

## Ручная проверка сценария

1. Выполните `/hh`, откройте OAuth-ссылку и разрешите доступ.
2. Нажмите `✍️ Подготовить отклик` в карточке вакансии.
3. Если резюме несколько, выберите нужное; оно станет выбором по умолчанию.
4. Проверьте название вакансии, компанию, резюме и полный текст письма.
5. Нажмите `✏️ Изменить письмо`, отправьте новый текст и проверьте обновленный preview.
6. Нажмите `📤 К подтверждению`: на этом шаге внешнего действия ещё нет.
7. Нажмите `🚀 Да, отправить на HH`: для обычной вакансии должен появиться результат успешной отправки через HH.
8. Повторите финальное нажатие: новый запрос в HH не должен выполняться.
9. Проверьте вакансию с тестом или внешней формой: бот должен показать специальное сообщение и официальный URL.
10. Проверьте базу: успешный отклик имеет `hh_applications.api_status=submitted`, а статус вакансии равен `applied_bot`.
11. На другой карточке нажмите `✅ Я уже откликнулся`, отмените подтверждение и убедитесь, что статус не изменился.
12. Повторите и подтвердите: статус должен стать `applied_manual`, `application_source=manual`, а вакансия должна исчезнуть из поиска и текущей подборки.
