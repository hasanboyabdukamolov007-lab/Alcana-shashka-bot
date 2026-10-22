# Deployment

The bot is a long-running polling process. It does not serve an administration website. If the platform sets `PORT`, the process also answers `GET /` with `{"status":"ok"}` so a health check can pass.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `BOT_TOKEN` | yes | Telegram bot token from BotFather |
| `ADMIN_TELEGRAM_ID` | yes | Numeric Telegram id of the only administrator |
| `DATABASE_URL` | yes | PostgreSQL URL. `postgres://` and `postgresql://` are accepted and converted to `postgresql+asyncpg://` |
| `APP_ENV` | no | `development` or `production` |
| `LOG_LEVEL` | no | Default `INFO`. Logs are JSON lines on stdout |
| `BACKUP_STORAGE_PATH` | no | Default `./backups` |
| `BACKUP_RETENTION_DAYS` | no | Default `7` |
| `BACKUP_HOUR_UTC` | no | Default `2` |

Do not commit `.env`.

Create the bot with BotFather, disable privacy mode if the group should see ordinary messages, and promote the bot to group admin with **Pin messages** enabled. Ban, delete, and anonymous-admin rights are unnecessary.

## DigitalOcean App Platform

`.do/app.yaml` describes the app. The component is a web service so the health check on `GET /` can pass. The process also polls Telegram and must stay running.

DigitalOcean Postgres connection strings include `sslmode=require`. The application translates that into the SSL setting used by asyncpg. `pg_dump` still receives the original libpq URL.

1. Push `main` to GitHub.
2. In DigitalOcean, open **Apps → Create App → GitHub** and select `hasanboyabdukamolov007-lab/Alcana-shashka-bot`, branch `main`.
3. DigitalOcean can read `.do/app.yaml`. If the form is manual, choose the Dockerfile, HTTP port `8080`, and health check path `/`.
4. Add a PostgreSQL 16 database in the same app. Bind its connection string to `DATABASE_URL`.
5. Set `BOT_TOKEN` and `ADMIN_TELEGRAM_ID` as encrypted environment variables. Set `APP_ENV=production`, `LOG_LEVEL=INFO`, `BACKUP_STORAGE_PATH=/app/backups`, `BACKUP_RETENTION_DAYS=7`, and `BACKUP_HOUR_UTC=2`.
6. Attach a 1 GB volume mounted at `/app/backups`.
7. Deploy and wait until the log contains `health endpoint listening` and `recovered tournament=`.
8. Open the app URL. It should return `{"status":"ok"}`. Then test `/admin` from the administrator account in Telegram.

Use the Frankfurt (`fra`) region when it is offered. One instance is enough. Do not scale the bot to more than one container, or two processes will poll the same bot token.

## Railway

1. Create a Railway project and add a PostgreSQL plugin.
2. Create a service from this repository. `railway.toml` selects the Dockerfile.
3. Set `BOT_TOKEN`, `ADMIN_TELEGRAM_ID`, `APP_ENV=production`, `LOG_LEVEL=INFO`, `BACKUP_STORAGE_PATH=/app/backups`, `BACKUP_RETENTION_DAYS=7`, and `BACKUP_HOUR_UTC=2`.
4. Set `DATABASE_URL` to the plugin URL. A `postgres://` URL is normalized at startup.
5. Add a volume mounted at `/app/backups` so dumps survive restarts. The volume is not a substitute for the database.
6. Deploy. The container runs `alembic upgrade head && python -m app.main`.
7. Railway injects `PORT`. The health response is available on that port.

The service must stay running. Do not use a one-shot cron service as the only bot process.

## Render

`render.yaml` describes a Docker web service and a PostgreSQL 16 database.

1. Create a Blueprint from this repository, or create the database and a Docker web service manually.
2. Set the secret environment variables `BOT_TOKEN` and `ADMIN_TELEGRAM_ID`.
3. Point `DATABASE_URL` at the Render Postgres connection string.
4. Attach a persistent disk mounted at `/app/backups`.
5. Health check path: `/`.
6. Deploy and wait until the log line shows the polling process recovered.

Render's free web instances sleep. A sleeping instance misses Telegram updates. Use a plan that stays awake.

## Local production-shaped run

```powershell
docker compose up --build
```

The database is `postgresql+asyncpg://shashka:shashka@db:5432/shashka` inside Compose. From the host, use `localhost` instead of `db`.

Apply migrations manually when you run the bot outside Compose:

```powershell
alembic upgrade head
python -m app.main
```

## Restore a PostgreSQL custom dump

Backups created with `pg_dump --format=custom` use the `.dump` extension.

Stop the bot first so it does not write during the restore.

```powershell
pg_restore --clean --if-exists --no-owner --dbname "postgresql://shashka:shashka@localhost:5432/shashka" backups\shashka-backup-YYYYMMDDTHHMMSSZ.dump
```

Then start the bot again. It reloads the live tournament, marks any in-flight `SENDING` notification as `UNCERTAIN`, and continues `PENDING` notifications.

Logical JSON backups, used when `pg_dump` is not installed:

```powershell
python scripts/restore.py backups\shashka-backup-YYYYMMDDTHHMMSSZ.json --force
```

`--force` is required while a tournament is still live. Restoration replaces rows. It is not the automatic restart path.

## Post-deployment checklist

1. Logs show a recovered tournament id or `tournament=None`, with no database connection error.
2. `GET /` on the platform port returns `{"status":"ok"}` when `PORT` is set.
3. From the administrator account, `/admin` opens the Uzbek panel. Another account receives "Bu amal faqat administrator uchun."
4. In the group, `/connect` saves the group. A second group can be connected. Only one tournament is live.
5. Create a tournament, choose the group, open registration, and confirm the group message is pinned and contains the prizes.
6. Register two participants in private chats. Confirm the group message updates instead of sending a second registration post.
7. Close registration, start, confirm pairings, and confirm one result. The standings message updates.
8. Finish or cancel that test tournament before a real event. A cancelled tournament must not name a champion.
9. Confirm `BACKUP_STORAGE_PATH` contains a new file after `python scripts/backup.py`, and files older than seven days are removed.
10. Restart the service and confirm the same tournament status is still shown in `/admin`.

Mocked automated tests do not prove this network checklist. Run it against the deployed bot.
