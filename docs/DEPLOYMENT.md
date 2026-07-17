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

## HeadHunter и OpenAI

HH и OpenAI не наследуют общие proxy-переменные по умолчанию. Это защищает от
ситуации, когда Telegram требует VPN, а HH отклоняет VPN-адрес.

```env
HH_PROXY_URL=
HH_TRUST_ENV=false
OPENAI_PROXY_URL=
OPENAI_TRUST_ENV=false
```

Чтобы использовать `HTTP_PROXY`, `HTTPS_PROXY` или `ALL_PROXY` для конкретного
HTTPX-провайдера, включите его `*_TRUST_ENV=true`. Явный `*_PROXY_URL` лучше,
потому что он не зависит от окружения процесса. Поддерживаются `http://`,
`https://`, `socks5://` и `socks5h://`.

HH GET-запросы повторяются при connect/read timeout, transport error, `429` и
временных `5xx`. Учитывается `Retry-After`, задержка ограничена и содержит
jitter. Обмен одноразового OAuth-кода и refresh token не повторяется после
неоднозначного read/write timeout: HH мог уже использовать код или заменить
refresh token. Это предотвращает повреждение OAuth-состояния.

`403` или CAPTCHA нельзя надежно «исправить» retry. `HH_PROXY_URL` позволяет
выбрать другой разрешенный маршрут, но приложение не обходит правила HH. Если
системный full-tunnel VPN уже перехватывает direct-трафик, для отдельного выхода
HH понадобится split tunneling или доступный proxy вне VPN.

OpenAI использует отдельный HTTPX transport, ограниченный timeout и retry
официального SDK. Пользователю показывается безопасная категория ошибки без
токена, сырого ответа или traceback.

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
- Временная недоступность внешнего провайдера отражается статусом `degraded`,
  но не превращает процесс в бесконечный restart loop.

Для liveness/restart probe используйте `/health/live`. Для снятия экземпляра с
пользовательского трафика — `/health/ready`.

## Локальный Docker Compose

1. Заполните `.env` и профиль в `data/`.
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

Если существующая SQLite-база раньше создавалась через `create_all`, сначала
сделайте резервную копию. Затем убедитесь, что модели совпадают со схемой:

```bash
alembic check
```

Только при результате `No new upgrade operations detected` можно принять
начальную миграцию без повторного создания таблиц:

```bash
alembic stamp 79d69bcda3e6
```

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

Formatter маскирует Telegram/OpenAI-токены, Bearer credentials, чувствительные
structured fields и пароль в proxy URL. Это дополнительная защита, а не замена
правильному хранению секретов. При подозрении на утечку ключ необходимо
отозвать и заменить.

## Границы текущей версии

Текущий продукт production-hardened как персональный однопользовательский
процесс, но еще не является multi-tenant SaaS для тысяч независимых клиентов:

- доступ разрешен одному `TELEGRAM_USER_ID`;
- часть доменных таблиц не содержит tenant key;
- long polling рассчитан на одну реплику;
- scheduler и блокировки находятся в памяти процесса;
- OAuth-токены HH хранятся в базе без application-level encryption at rest;
- нет очереди задач, Redis-lock и webhook ingress;
- публичный HH API не документирует автоматическую отправку отклика соискателя.

Для настоящего SaaS следующим архитектурным этапом должны стать tenant-aware
schema и authorization, webhook mode, очередь workers, Redis/distributed locks,
шифрование OAuth-токенов с KMS и независимое масштабирование API/workers. Эти
изменения требуют продуктовой модели пользователей и не могут быть безопасно
«додуманы» внутри текущего приватного бота.
