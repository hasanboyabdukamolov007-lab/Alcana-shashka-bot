# Shashka Tournament Bot

Telegram bot for running an offline checkers (shashka) tournament. Participants and the single administrator use Telegram only. There is no web admin panel.

The interface is Uzbek Latin. Pairing, scoring, and prize rules are project rules for a 2/1/0 Swiss scale. They are not a claim of federation compliance.

## Rules that affect results

Swiss points: win 2, draw 1, loss 0. A pairing bye awards 2 points and is not a real victory. A technical win awards 2 points and is not a real victory.

Hard pairing constraints:

1. A complete matching, with one bye when the active count is odd.
2. No repeated opponents.
3. The bye goes to someone with the fewest previous byes.

Among those pairings the engine minimizes the bye recipient's score, then the sum of absolute score differences. Edmonds' algorithm (`networkx.max_weight_matching`) chooses an exact maximum-cardinality maximum-weight matching. Equal optima are separated by a saved random seed. If no legal pairing exists, the administrator must explicitly approve early termination. Repeated opponents are never inserted silently.

Buchholz uses the opponent's final Swiss score for a played game. For a bye or technical result in completed round `r`, with horizon `H` equal to the number of Swiss rounds actually played:

```text
virtual_score = score_before_round + (H - r + 1) * 1
```

Sonneborn-Berger weights that contribution by 1, 0.5, or 0 for 2, 1, or 0 points. Prize places that are tied on Swiss points are decided by playoff games. Playoff points do not change Swiss points.

## Requirements

- Python 3.12 or newer
- PostgreSQL 16 for production
- A Telegram bot token and the administrator's numeric Telegram id

Automated tests use SQLite so they do not need PostgreSQL. Production migrations and the Docker Compose stack use PostgreSQL.

## Local setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
```

Edit `.env`:

- `BOT_TOKEN`
- `ADMIN_TELEGRAM_ID`
- `DATABASE_URL` (see `.env.example`)
- `APP_ENV`
- `LOG_LEVEL`
- `BACKUP_STORAGE_PATH`
- `BACKUP_RETENTION_DAYS`
- `BACKUP_HOUR_UTC`

Start PostgreSQL and the bot:

```powershell
docker compose up -d db
alembic upgrade head
python -m app.main
```

Or start both services:

```powershell
docker compose up --build
```

The Compose file reads `.env` and overrides `DATABASE_URL` so the bot reaches the `db` service.

## Administrator workflow

1. Add the bot to a group as an administrator with permission to pin messages. Other moderation rights are not required.
2. Send `/connect` in that group.
3. Open a private chat, send `/admin`, and create a tournament.
4. Choose the group, limits, and prizes. Review the summary.
5. Open registration. The group gets one pinned registration message.
6. Participants register in private with a first name and surname. A full list moves new players to a waiting list.
7. Close registration, start the tournament, review pairings, and confirm one variant.
8. Enter every result and confirm it. Review the round, then start the next round.
9. Resolve prize ties if Swiss points do not separate 1st, 2nd, and 3rd.
10. Approve the final standings. The bot publishes them and queues congratulations.

`/menu` opens the participant menu: Mening profilim, Mening raqibim, Mening o‘yinlarim, Jonli reyting, Turnir holati, Oldingi turnirlar, Mukofotlar.

## Checks

```powershell
ruff check app tests scripts
pytest
```

Mocked tests do not prove live Telegram delivery. They prove the pairing, scoring, database, and outbox behavior.

## Backups

The running bot creates a daily backup at `BACKUP_HOUR_UTC` and keeps files for `BACKUP_RETENTION_DAYS` (default 7). The Docker image includes `pg_dump`. Without `pg_dump`, the service writes a logical JSON backup.

```powershell
python scripts/backup.py
python scripts/restore.py backups\shashka-backup-YYYYMMDDTHHMMSSZ.json --force
```

A custom-format dump is restored with `pg_restore`, documented in `DEPLOYMENT.md`. Restarting the process reloads the live tournament from PostgreSQL. That is not recovery from a lost database.

## Project layout

- `app/domain` — pairing, scoring, prizes, and state transitions
- `app/services` — registration, rounds, outbox, backups
- `app/telegram` — handlers and group messages
- `app/jobs` — outbox and backup loops
- `alembic` — schema migration
- `tests` — pytest
