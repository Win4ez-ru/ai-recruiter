# AI Recruiter — персональный Telegram-бот для поиска iOS-вакансий

AI Recruiter получает свежие вакансии через официальный API HeadHunter, удаляет дубликаты, дешево отсеивает нерелевантные позиции, оценивает оставшиеся вакансии через OpenAI и отправляет лучшие варианты в приватный Telegram-чат. Текущая версия рассчитана на одного пользователя, но имеет переносимый production-контур: provider-specific proxy, retry/backoff, health checks, graceful shutdown, Alembic, PostgreSQL, Docker и cross-platform CI.

Подробный runbook по proxy/VPN, Docker, PaaS, миграциям и честным границам масштабирования находится в [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Коротко: как начать пользоваться

1. Создайте бота через [@BotFather](https://t.me/BotFather) и сохраните токен.
2. Узнайте свой числовой Telegram ID по инструкции ниже.
3. Установите Python 3.11+, скопируйте `.env.example` в `.env` и заполните четыре обязательных значения.
4. Установите зависимости командой `pip install -r requirements-dev.txt` для разработки или `pip install -r requirements.txt` для runtime.
5. Запустите `python run.py`, откройте созданного бота в Telegram и отправьте `/start`.
6. Пока нужен бот, процесс `python run.py` должен продолжать работать. Для режима 24/7 разместите его на VPS или домашнем сервере.

Telegram-бот не нужно отдельно «загружать» в Telegram: BotFather создает его учетную запись, а запущенная Python-программа подключается к этой учетной записи по токену и получает команды через polling.

## Проект для портфолио

Пример формулировки для резюме:

> Разработал асинхронного Telegram-агента для поиска iOS-вакансий: интеграция с HeadHunter API, rule-based фильтрация, LLM-ранжирование через Structured Outputs, персональные сопроводительные письма, SQLite/SQLAlchemy, APScheduler и защита от повторной обработки.

На собеседовании можно отдельно рассказать про устойчивость внешних API, дедупликацию, Pydantic-валидацию LLM-ответов, разделение на repositories/services/sources и тестирование без реальных сетевых запросов.

## Возможности

- семь отдельных поисковых запросов для iOS, Swift, SwiftUI и стажировок;
- вакансии за последние 7 дней, Санкт-Петербург и удаленный формат;
- пагинация, retry/backoff и таймауты для HH API;
- автоматический Telegram failover между direct, HTTP и SOCKS proxy;
- отдельные proxy и timeout-политики для Telegram, HH и OpenAI;
- обработка `Retry-After`, rate limit, `5xx` и transport timeout;
- JSON-логи в stdout с редактированием секретов;
- liveness/readiness endpoints и graceful shutdown по `SIGINT`/`SIGTERM`;
- SQLite для локального режима, PostgreSQL/asyncpg и Alembic для deployment;
- дедупликация по `source + external_id`;
- предварительный фильтр без LLM;
- структурированный LLM-анализ с оценкой 0–100 и Pydantic-валидацией;
- индивидуальное сопроводительное письмо;
- подключение аккаунта HeadHunter через OAuth 2 Authorization Code + PKCE;
- синхронизация и выбор резюме HeadHunter;
- черновик отклика, редактирование письма и два отдельных подтверждения;
- одноразовые подтверждения и защита от конкурентной обработки;
- статусы `new`, `saved`, `applied`, `interview`, `test_task`, `rejected`, `offer`, `skipped`;
- SQLite и асинхронный SQLAlchemy 2;
- ручной поиск и автоматический запуск по расписанию;
- защита от повторного анализа и повторной отправки;
- приватный доступ только для заданного Telegram User ID.

## Архитектура

Приложение представляет собой один асинхронный composition root с разделенными transport, repository и service слоями:

1. `HHClient` делает запросы только к официальному `api.hh.ru`, объединяет выдачу по Санкт-Петербургу и удаленному формату.
2. `VacancySearchService` дедуплицирует результаты, получает полные карточки и сохраняет новые вакансии.
3. `VacancyFilter` исключает очевидно неподходящие позиции обычными правилами Python.
4. `VacancyRanker` вызывает OpenAI Responses API и получает Structured Output по Pydantic-схеме.
5. Репозитории сохраняют анализы и пользовательские статусы в SQLite.
6. `DigestService` форматирует безопасные HTML-карточки и отмечает успешно отправленные вакансии.
7. aiogram обрабатывает команды и inline-кнопки, APScheduler запускает тот же workflow по расписанию.
8. `HHOAuthService` проверяет одноразовый `state`, обменивает OAuth-код с PKCE и обновляет токены.
9. `HHApplicationService` готовит черновик и атомарно обрабатывает финальное подтверждение.
10. `FailoverTelegramSession` изолирует сетевые маршруты Telegram, а `HHClient` и OpenAI используют собственные HTTPX transports.
11. `HealthRegistry` и общий HTTP-сервер обслуживают OAuth callback, liveness и readiness.

Сбой одной вакансии или внешнего API логируется и не останавливает процесс. Неоднозначные non-idempotent операции не повторяются вслепую, чтобы не создавать дубликаты или не повреждать OAuth-состояние.

## Требования

- Python 3.11 или новее;
- Telegram-бот и его токен;
- OpenAI API key и доступная модель со Structured Outputs;
- интернет-доступ к `api.hh.ru`, `api.openai.com` и Telegram Bot API.

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
OPENAI_API_KEY=sk-...
OPENAI_MODEL=модель_с_Structured_Outputs
DATABASE_URL=sqlite+aiosqlite:///./job_agent.db
DATABASE_AUTO_CREATE=true
HH_USER_AGENT=KirillJobAgent/1.0 (your-email@example.com)
HH_CLIENT_ID=
HH_CLIENT_SECRET=
HH_REDIRECT_URI=http://localhost:8080/callback
HTTP_HOST=127.0.0.1
HTTP_PORT=8080
SEARCH_INTERVAL_HOURS=12
MIN_SCORE_TO_SEND=65
MAX_VACANCIES_PER_DIGEST=10
LOG_FORMAT=json
LOG_FILE_ENABLED=false
~~~

Полный список timeout, retry, proxy, health, pool и logging параметров находится в `.env.example`. `OPENAI_MODEL` должен быть доступен вашему API-проекту и поддерживать Structured Outputs. Для HH рекомендуется указывать название приложения и контактный email в User-Agent.

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

Профиль находится в `data/candidate_profile.json`, текст резюме — в `data/resume.txt`. Замените пример резюме и строку контактов на актуальные данные до генерации сопроводительных писем. JSON должен оставаться валидным и соответствовать существующей структуре.

После изменения файлов перезапустите процесс: профиль загружается при старте.

## Запуск

~~~bash
python run.py
~~~

При локальном `DATABASE_AUTO_CREATE=true` таблицы создаются автоматически. В production сначала выполните `alembic upgrade head` и установите `DATABASE_AUTO_CREATE=false`. По умолчанию логи выводятся как JSON в stdout; файловая ротация включается отдельно.

Docker Compose:

~~~bash
docker compose up --build -d
docker compose logs -f job-agent
~~~

Подробные сценарии запуска на VPS, Railway, Render и cloud-платформах описаны в [deployment runbook](docs/DEPLOYMENT.md).

## Команды Telegram

- `/start` — описание и список команд;
- `/help` — справка;
- `/search` — полный ручной поиск, фильтрация, анализ и отправка дайджеста;
- `/new` — еще не отправленные вакансии с оценкой выше порога;
- `/top` — лучшие вакансии, включая ранее показанные;
- `/saved` — вакансии со статусом `saved`;
- `/applied` — вакансии со статусом `applied`;
- `/stats` — сводная статистика и наиболее частые пробелы в навыках;
- `/profile` — краткий профиль и пути к редактируемым файлам.
- `/hh` — состояние подключения или запуск OAuth HeadHunter.

Под карточкой есть кнопка `📝 Подготовить отклик`. Бот проверяет OAuth, синхронизирует резюме, предлагает выбор, генерирует или использует сохраненное письмо и показывает полный предварительный просмотр. Если защитный шлюз HH отклоняет чтение собственных резюме с общим `403 forbidden`, бот не обходит ограничение: он готовит письмо по локальному профилю и переносит выбор резюме на официальную страницу HeadHunter. Письмо можно изменить через FSM. Первое подтверждение только создает одноразовый токен; обработка начинается после `🚀 Да, отправить`.

### Ограничение официального API HH

Публичная OpenAPI-спецификация HeadHunter на момент реализации документирует OAuth, `POST /token`, `GET /me`, доступ к резюме по `resumes_url`, `GET /resumes/{id}` и чтение откликов, но не содержит операции `POST` для создания отклика соискателем. У `/negotiations` документирован только `GET`.

Бот не вызывает старые или недокументированные endpoint'ы. После финального подтверждения черновик получает статус `manual_action_required`, а пользователь получает кнопку официальной формы HeadHunter. Для обязательного теста, внешней формы или архивной вакансии показывается отдельное объяснение. Статус `applied` не устанавливается без подтвержденного успешного ответа официального API.

## Тесты

~~~bash
python -m pytest
~~~

Внешние API в тестах заменены моками. Проверяются фильтрация, HH-клиент, HTML-форматирование, дедупликация, смена статуса и защита дайджеста от повторной отправки.

Дополнительно проверяются OAuth/PKCE, refresh-токены, получение резюме, черновики, FSM-редактирование, два подтверждения, просроченный и чужой токен, последовательная и конкурентная идемпотентность, timeout/rate limit, ручной fallback и запрет преждевременного статуса `applied`.

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
│   ├── candidate_profile.json
│   └── resume.txt
├── tests/
├── migrations/           # Alembic revisions
├── docs/                 # deployment runbook
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

### OpenAI недоступен

Проверьте ключ, модель, баланс и доступ проекта. OpenAI имеет отдельные timeout/retry/proxy настройки. Provider-level сбой останавливает дальнейшие LLM-вызовы текущего поиска, но не завершает процесс; необработанные вакансии будут рассмотрены позднее.

### Telegram не принимает сообщение

Убедитесь, что пользователь первым написал боту и не заблокировал его. При сетевой блокировке задайте `TELEGRAM_PROXY_URLS`; несколько URL образуют failover-цепочку. Системный VPN работает без изменения кода. Если все маршруты недоступны, процесс остается жив и продолжает backoff, но рабочий внешний маршрут все равно необходим.

### База заблокирована

Не запускайте несколько копий приложения с одним SQLite-файлом. Остановите лишний процесс и проверьте права записи в каталог проекта.

## Границы текущей версии

- один пользователь и один профиль;
- SQLite подходит только для одной реплики; PostgreSQL и Alembic поддерживаются для deployment;
- только официальный HeadHunter API;
- токены OAuth хранятся в базе без application-level шифрования;
- создание отклика через API недоступно, пока операция отсутствует в публичной OpenAPI HH; используется официальный ручной URL;
- качество ранжирования и писем зависит от выбранной OpenAI-модели;
- простой rule-based фильтр может давать пограничные ложные срабатывания;
- статистика статусов `interview`, `test_task`, `rejected` и `offer` хранится в модели, но текущий UI не содержит отдельных Telegram-кнопок для всех этих переходов;
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

Секреты читаются только из окружения. Бот не хранит логин или пароль HeadHunter, не обходит ограничения площадки, не собирает скрытые данные HR и не использует браузерную автоматизацию. Никакое действие не начинается без двух явных подтверждений пользователя. Пока официальный публичный API не предоставляет документированный POST создания отклика, финальное действие выполняется пользователем на странице вакансии.

## Ручная проверка сценария

1. Выполните `/hh`, откройте OAuth-ссылку и разрешите доступ.
2. Нажмите `📝 Подготовить отклик` в карточке вакансии.
3. Если резюме несколько, выберите нужное; оно станет выбором по умолчанию.
4. Проверьте название вакансии, компанию, резюме и полный текст письма.
5. Нажмите `✏️ Изменить письмо`, отправьте новый текст и проверьте обновленный preview.
6. Нажмите `✅ Отправить отклик`: на этом шаге внешнего действия еще нет.
7. Нажмите `🚀 Да, отправить`: при текущем API появится ручной сценарий и кнопка HeadHunter.
8. Повторите финальное нажатие: повторная обработка должна быть отклонена.
9. Проверьте вакансию с тестом или внешней формой: бот должен показать специальное сообщение и официальный URL.
10. Проверьте базу: `hh_applications.api_status` равен `manual_action_required`, а старый статус вакансии не равен `applied`.
