# Production deployment и устойчивость сети

Этот документ описывает эксплуатационный контур AI Recruiter: сетевые
маршруты, proxy/VPN, health checks, миграции, Docker и ограничения текущей
архитектуры.

## Принятое сетевое решение

Есть четыре практических способа дать приложению доступ к внешним API:

1. Системный VPN или split tunneling. Код менять не нужно, но правила зависят
   от ОС и VPN-клиента. Обычный full-tunnel направляет через VPN также HH и
   OpenAI, что иногда приводит к дополнительным ограничениям провайдеров.
2. Общие `HTTP_PROXY`, `HTTPS_PROXY` и `ALL_PROXY`. Это удобно для одного
   маршрута, но не позволяет надежно разделить Telegram, HH и OpenAI.
3. Отдельный transport и proxy для каждого провайдера. Это выбранный базовый
   вариант: Telegram может идти через SOCKS5, а HH — напрямую или через другой
   egress.
4. Service mesh, NAT gateway или отдельный egress-proxy. Это подход для
   кластерной инфраструктуры; приложение совместимо с ним через те же
   переменные окружения.

Приложение не устанавливает VPN самостоятельно. VPN работает ниже HTTP-слоя и
управляется ОС, контейнерной сетью или инфраструктурой. Передавать конфигурацию
произвольного VPN в `.env` и запускать его из процесса бота было бы
непереносимо и небезопасно. Для приложения VPN прозрачен и не требует изменения
кода.

Никакой код не гарантирует доступ, если сеть блокирует Telegram и все
настроенные proxy либо если сам провайдер отклоняет используемый IP. В таком
случае нужен рабочий внешний маршрут: proxy, split tunnel, NAT gateway или VPS
в подходящей сети.

## Telegram: direct, proxy и failover

Пример двух резервных маршрутов:

```env
TELEGRAM_DIRECT_ENABLED=true
TELEGRAM_PROXY_URLS=socks5://user:password@proxy-1.example:1080,http://proxy-2.example:8080
TELEGRAM_REQUEST_TIMEOUT_SECONDS=30
TELEGRAM_ROUTE_COOLDOWN_SECONDS=60
TELEGRAM_POLLING_BACKOFF_MIN_SECONDS=1
TELEGRAM_POLLING_BACKOFF_MAX_SECONDS=30
```

Поддерживаются `http://`, `socks4://` и `socks5://`. Сначала используется
direct-маршрут, затем proxy в указанном порядке. Неисправный маршрут уходит в
cooldown, успешный становится активным. После cooldown direct снова может
стать предпочтительным.

Автоматический повтор одного Telegram-запроса на другом маршруте выполняется
только для безопасных методов (`getUpdates`, `getMe`, настройка команд и
webhook). `sendMessage` после неоднозначного сетевого сбоя автоматически не
повторяется: сервер мог принять сообщение до разрыва соединения, и слепой retry
создал бы дубликат. Неисправный маршрут помечается, а следующий запрос идет по
резервному каналу.

Если Telegram недоступен при старте, процесс остается жив, пишет причину и
повторяет подключение с exponential backoff. Ошибка токена не маскируется как
сетевой сбой и остается фатальной конфигурационной ошибкой.

При системном VPN оставьте `TELEGRAM_DIRECT_ENABLED=true`. Приложение будет
использовать системный маршрут без изменения кода. На Docker Desktop поведение
host VPN зависит от конкретного VPN-клиента; явный proxy обычно
предсказуемее.

## HeadHunter и AI-провайдеры

HH и AI-провайдеры не наследуют общие proxy-переменные по умолчанию. Это защищает от
ситуации, когда Telegram требует VPN, а HH отклоняет VPN-адрес.

```env
HH_PROXY_URL=
HH_TRUST_ENV=false
HH_DEFAULT_RESUME_ID=
OPENAI_PROXY_URL=
OPENAI_TRUST_ENV=false
YANDEX_PROXY_URL=
YANDEX_TRUST_ENV=false
```

Чтобы использовать `HTTP_PROXY`, `HTTPS_PROXY` или `ALL_PROXY` для конкретного
HTTPX-провайдера, включите его `*_TRUST_ENV=true`. Явный `*_PROXY_URL` лучше,
потому что он не зависит от окружения процесса. Поддерживаются `http://`,
`https://`, `socks5://` и `socks5h://`.

HH GET-запросы повторяются при connect/read timeout, transport error, `429` и
временных `5xx`. Учитывается `Retry-After`, задержка ограничена и содержит
jitter. Поиск вакансий использует application-токен, полученный по
`client_credentials` из `HH_CLIENT_ID` и `HH_CLIENT_SECRET`. Токен кешируется
на время жизни процесса; при его отзыве клиент один раз получает новый токен и
повторяет безопасный GET. Обмен одноразового OAuth-кода и refresh token не повторяется после
неоднозначного read/write timeout: HH мог уже использовать код или заменить
refresh token. Это предотвращает повреждение OAuth-состояния.

`403` или CAPTCHA нельзя надежно «исправить» retry. `HH_PROXY_URL` позволяет
выбрать другой разрешенный маршрут, но приложение не обходит правила HH. Если
системный full-tunnel VPN уже перехватывает direct-трафик, для отдельного выхода
HH понадобится split tunneling или доступный proxy вне VPN.

Если OAuth соискателя действителен, но защитный шлюз HH отвечает общим
`403 forbidden` только на чтение `/resumes/mine`, подготовка письма продолжится
по локальному профилю. Если задан `HH_DEFAULT_RESUME_ID`, бот использует это
резюме для официального `POST /negotiations/response`; иначе пользователь
выбирает резюме на официальной странице HH. Идентификатор берется из URL
собственного резюме и не является OAuth-секретом. В структурированном событии
`hh_resume_sync_rejected` сохраняется `request_id`, который можно передать
поддержке API HeadHunter.

POST отклика не повторяется после read/write timeout: результат внешней операции
может быть неизвестен. Пользователь получает официальный URL и должен проверить
список откликов перед новой попыткой. `already_applied` нормализуется как успешно
синхронизированный результат.

OpenAI и YandexGPT используют отдельные HTTPX transports и ограниченные
timeout/retry. Пользователю показывается общая безопасная категория AI-ошибки
без токена, сырого ответа или traceback.

`AI_RANKING_CONCURRENCY=3` ограничивает число одновременных облачных ranking-
запросов. Для `AI_PROVIDER=ollama` composition root принудительно использует
concurrency `1`, чтобы локальная модель не конкурировала сама с собой за RAM.
Structured logs содержат длительности HH/AI, число реальных AI-вызовов и cache
hits.

Рекомендуемая конфигурация Yandex AI Studio:

```env
AI_PROVIDER=yandex
YANDEX_API_KEY=
YANDEX_FOLDER_ID=
YANDEX_MODEL=yandexgpt-5.1
YANDEX_BASE_URL=https://ai.api.cloud.yandex.net/v1
YANDEX_DATA_LOGGING_ENABLED=false
AI_RANKING_CONCURRENCY=3
```

Для долгоживущего процесса используйте сервисный аккаунт с ролью
`ai.languageModels.user` и создайте API-ключ через интерфейс AI Studio по
актуальной инструкции. При ручной автоматизации сверяйте требуемый scope:
разные страницы Yandex сейчас называют `yc.ai.languageModels.execute` и
`yc.ai.foundationModels.execute`. IAM token с коротким сроком жизни без
автоматического refresh не подходит. `x-data-logging-enabled: false` включён
по умолчанию, поскольку запрос содержит профиль и резюме.

Локальный режим не требует OpenAI API key:

```env
AI_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:4b-instruct
OLLAMA_TIMEOUT_SECONDS=180
OLLAMA_MAX_RETRIES=2
OLLAMA_CONTEXT_LENGTH=16384
```

Ollama вызывается через нативный `/api/chat`. Для ранжирования приложение
передает JSON Schema и повторно валидирует результат через Pydantic. При запуске
бота внутри Docker Compose используется `http://host.docker.internal:11434`,
поскольку `127.0.0.1` контейнера не является хостом macOS. Ollama должна быть
запущена на хосте до старта бота, а модель заранее загружена командой
`ollama pull qwen3:4b-instruct`.

## Health checks

HTTP-сервер OAuth и health endpoints использует один порт:

```env
HTTP_HOST=0.0.0.0
HTTP_PORT=8080
HEALTHCHECK_ENABLED=true
HEALTH_LIVE_PATH=/health/live
HEALTH_READY_PATH=/health/ready
```

PaaS-переменная `PORT` имеет приоритет над `HTTP_PORT`.

- `GET /health/live` возвращает `200`, пока процесс и event loop обслуживают
  HTTP-сервер.
- `GET /health/ready` возвращает `503` до завершения внутренней инициализации и
  во время остановки.
- Внешние HH/AI ошибки отражаются в результате конкретной операции и structured
  logs, но не делают liveness отрицательным и не запускают restart loop.

Для liveness/restart probe используйте `/health/live`. Для снятия экземпляра с
пользовательского трафика — `/health/ready`.

## Локальный Docker Compose

1. Заполните `.env` и рабочие профиль/резюме в `data/`.
2. Соберите и запустите сервис:

```bash
docker compose up --build -d
docker compose logs -f job-agent
```

Остановка:

```bash
docker compose down
```

SQLite хранится в named volume `job_agent_data`, а не внутри disposable-слоя
контейнера. Compose запускает ровно один polling-экземпляр и передает `SIGTERM`
через init-процесс.

Рабочие `data/candidate_profile.json` и `data/resume.txt` исключены из Docker
build context и не попадают в image layers. Compose монтирует локальный `data/`
read-only; на PaaS используйте secret files или отдельный read-only volume.

## База данных и миграции

Для локальной разработки допустимы:

```env
DATABASE_URL=sqlite+aiosqlite:///./job_agent.db
DATABASE_AUTO_CREATE=true
```

Production должен применять Alembic до старта приложения:

```bash
alembic upgrade head
python run.py
```

и использовать:

```env
DATABASE_AUTO_CREATE=false
```

Docker entrypoint делает это автоматически при
`RUN_DATABASE_MIGRATIONS=true`.

Для PostgreSQL принимаются как полный SQLAlchemy URL, так и обычные PaaS URL:

```env
DATABASE_URL=postgresql+asyncpg://user:password@db.example:5432/job_agent
```

Значения `postgres://...` и `postgresql://...` автоматически нормализуются к
asyncpg. Пароль БД считается секретом и не появляется в `repr(Settings)`.

Если существующая база раньше создавалась через `create_all` и не содержит
`alembic_version`, сначала сделайте резервную копию. Нельзя вслепую делать
`stamp initial`: более поздние миграции попытаются повторно создать уже
существующие таблицы.

Сначала выполните read-only сравнение реальной схемы со всеми текущими
SQLAlchemy-моделями:

```bash
python -m scripts.adopt_legacy_database
```

Только при точном совпадении команда предложит безопасно принять текущую head
revision:

```bash
python -m scripts.adopt_legacy_database --stamp
alembic check
```

Если найдены отличия, stamp запрещён: перенесите данные в новую базу или
подготовьте отдельную data migration. Скрипт никогда не меняет схему и без
явного `--stamp` работает read-only.

## VPS, Railway, Render и облака

Рекомендуемый общий путь:

1. Собирать предоставленный `Dockerfile`.
2. Передавать секреты через secret manager платформы, а не загружать `.env`.
3. Использовать `HTTP_HOST=0.0.0.0`; порт платформа передает через `PORT`.
4. Для SQLite подключить persistent disk и оставить один экземпляр. Для более
   надежного хранения использовать managed PostgreSQL.
5. Настроить health URL `/health/live`.
6. Оставить количество реплик равным `1`: Telegram long polling одного токена
   нельзя одновременно выполнять несколькими экземплярами без координации.
7. Для HH OAuth назначить публичный HTTPS-домен и зарегистрировать точный URL,
   например `https://bot.example.com/oauth/hh/callback`.

HTTP-сервер приложения не завершает TLS самостоятельно. На VPS поставьте перед
ним Caddy, Nginx, Traefik или cloud load balancer. На PaaS TLS обычно завершает
сама платформа.

Если в кабинете HH уже зарегистрирован
`http://localhost:8080/callback`, оставьте в `.env` ровно это же значение:

```env
HH_REDIRECT_URI=http://localhost:8080/callback
```

Приложение автоматически зарегистрирует путь `/callback`. Host и port, на
которых слушает сервер, задаются отдельно через `HTTP_HOST` и `HTTP_PORT`.
Redirect URI должен посимвольно совпадать со значением в кабинете HH.

## Логи и секреты

По умолчанию приложение пишет JSON Lines в stdout:

```env
LOG_LEVEL=INFO
LOG_FORMAT=json
LOG_FILE_ENABLED=false
```

Это подходит Docker, systemd, Railway, Render и cloud logging. Локальный
ротационный файл можно включить через `LOG_FILE_ENABLED=true` и
`LOG_FILE_PATH=logs/job-agent.log`.

Formatter маскирует Telegram/AI-токены, Bearer credentials, чувствительные
structured fields и пароль в proxy URL. Это дополнительная защита, а не замена
правильному хранению секретов. При подозрении на утечку ключ необходимо
отозвать и заменить.

## Границы текущей версии

Текущий продукт production-oriented как персональный однопользовательский
процесс, но ещё не является multi-tenant SaaS для независимых клиентов:

- доступ разрешен одному `TELEGRAM_USER_ID`;
- часть доменных таблиц не содержит tenant key;
- long polling рассчитан на одну реплику;
- scheduler и блокировки находятся в памяти процесса;
- OAuth-токены HH хранятся в базе без application-level encryption at rest;
- нет очереди задач, Redis-lock и webhook ingress;
- вакансии с тестом или внешней формой требуют ручного завершения отклика.

Для настоящего SaaS следующим архитектурным этапом должны стать tenant-aware
schema и authorization, webhook mode, очередь workers, Redis/distributed locks,
шифрование OAuth-токенов с KMS и независимое масштабирование API/workers. Эти
изменения требуют продуктовой модели пользователей и не могут быть безопасно
«додуманы» внутри текущего приватного бота.
