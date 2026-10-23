# 📊 Finance Control Telegram Bot

[![Tests](https://github.com/KonstantinS343/Finance-telegram-bot/actions/workflows/ci.yaml/badge.svg)](https://github.com/KonstantinS343/Finance-telegram-bot/actions/workflows/ci.yaml)
[![Security](https://github.com/KonstantinS343/Notes/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/KonstantinS343/Notes/actions/workflows/github-code-scanning/codeql)
[![GitHub license](https://img.shields.io/github/license/range-of-motion/budget.svg)](https://github.com/KonstantinS343/Finance-telegram-bot/blob/main/LICENSE)

Manage your finances with ease with the help of the telegram bot **Finance Control**! This bot provides you with convenient tools for creating personalized categories, adding income 💰 and expenses 💸, as well as viewing reports in the form of tables 📊 and pie charts 📈.

## Main functions

📌 **Create Categories:** Personalize your financial system by creating your own categories for expenses and income. This will help you analyze your finances in more detail.

💰 **Adding income and expenses:** Just send the bot information about your financial transactions. Don't miss a single transaction!

📊 **Reports and Analysis:** Get clear reports on the state of your finances. View the data in the form of convenient tables and visual pie charts. This will help you better understand where your money is going and how to increase your income.

🌐 **Language change:** Now you can choose a convenient interface language. Russian 🇷🇺, Belarusian 🇧🇾 and English 🇬🇧 languages are available to make interaction with the bot as comfortable as possible.

⚡ **Quick entry:** type `500 кафе обед` (expense with a comment), `+50000 зарплата` (income) or `1200+350*2 продукты` (calculator, shown before saving). Unknown or similar categories are confirmed with buttons; `FEATURE_QUICK_INPUT=0` turns it off. The calculator also works in the amount step of the buttons.

📜 **History and edits:** `/history` (or the 📜 button) lists operations 5 per page with period and category filters; open one to change its amount, category, comment or date, or delete it after a confirmation. Every saved operation gets a ↩️ Undo button, and `/undo` offers to delete the last one. Dates are shown and typed in `TIMEZONE` (default UTC). `FEATURE_HISTORY=0` turns it off.

🎯 **Budgets:** `/budget` (or the 🎯 button) shows this month's progress for an overall limit and per-category limits, what is left and how much you can spend per day. After every expense or edit the bot queues a Celery check; at 80% and 100% the worker sends a notification, at most once per threshold per month (a unique key in `budget_alerts` guarantees it). `FEATURE_BUDGETS=0` turns it off. Months follow `TIMEZONE`.

📈 **Analytics:** `/stats` (or the 📈 button) shows a month at a time: income, expenses, savings rate, average spending per day, a forecast for the month end and how much you can spend per day (by the overall budget if set, otherwise from income left); a per-category comparison with the previous month (amount and %), top categories and the largest expenses; ◀️ ▶️ switch months and a button sends a 12-month income/expense chart. `/report` now offers today / week / month / year (calendar periods in `TIMEZONE`) or a custom range like `01.09.2026-30.09.2026`; a report spanning several months also gets a monthly chart. `FEATURE_ANALYTICS=0` hides `/stats`.

🔁 **Recurring operations:** `/recurring` (or the 🔁 button) keeps templates such as a salary or a bill: daily, weekly or monthly (a monthly 31st stays on the last day of shorter months), a start date and an optional reminder 1/3/7 days before. The `beat` container runs a job every 10 minutes; due operations are created (missed days are caught up), each with ↩️ Undo, and reminders offer ⏭ Skip or a new amount. A compare-and-set on the template's next date makes double creation impossible. `FEATURE_RECURRING=0` turns it off.

⭐ **Savings goals:** `/goals` (or the ⭐ button): a name, a target and an optional deadline; progress bar, what is left and how much to put aside per month to make it on time; top up or withdraw from the bot. Money in goals is tracked separately and does not change the balance. `FEATURE_GOALS=0` turns it off.

📤 **Export and backups:** `/export` sends operations for a period as CSV (UTF-8 with BOM, `;`, opens in Excel) or XLSX, built in memory. Every day at `BACKUP_HOUR` the worker runs `pg_dump` (client 15 in the image), gzips it to `./backups`, keeps the newest `BACKUP_KEEP` and sends the file to you; `/backup` makes one now. Restore: stop the bot and run `restore.bat backups\finance-….sql.gz` (Windows), or `gunzip -c FILE | docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'`. The dump uses `--clean --if-exists`, so it replaces the current data.

💱 **Several currencies:** set `BASE_CURRENCY` (e.g. `UZS`) and type `50 usd кафе`, `50$ отель` or `20 евро такси`: the amount is converted by the day's rate from the free [fawazahmed0 currency-api](https://github.com/fawazahmed0/exchange-api) (no key, historical rates, a fallback mirror, cached in Redis), shown for confirmation and stored in the base currency together with the original amount, currency and rate. Balance, budgets, analytics and reports stay in the base currency; history, "last 10" and exports show the original. Editing the amount in history switches the operation to the base currency. Empty `BASE_CURRENCY` keeps the bot single-currency.

🎙 **Voice and receipts (offline):** with `FEATURE_VOICE=1` a voice message like «пятьсот кафе обед» is recognised by [Vosk](https://alphacephei.com/vosk/) (small Russian model, ffmpeg) and number words become digits; with `FEATURE_RECEIPTS=1` a receipt photo is read by Tesseract and the total is found by keywords (ИТОГО, К ОПЛАТЕ, TOTAL, JAMI…). Both go through quick entry and always ask before saving; a photo caption can name the category. `start.bat` builds these components into the image only when the flags are on.

| Option | Cost | Privacy | Quality |
|---|---|---|---|
| **Vosk + Tesseract (used)** | free | everything stays on your computer | good for short phrases; receipts depend on photo quality |
| Cloud speech (OpenAI Whisper API, Google, Yandex SpeechKit) | ~$0.006/min and up | audio sent to the provider | better on noise and accents |
| Cloud vision / LLM for receipts | ~$0.002–0.01 per photo | receipt image sent to the provider | best at reading crumpled receipts and line items |

🌍 **Public bot, languages and currencies:** with `ACCESS_MODE=public` anyone can use the bot; every query is scoped to the user, so people never see each other's data, and a flood guard drops spam. On the first start a person picks a language (Русский, O'zbekcha, English, Беларуская) and a main currency (so'm, rubles, Belarusian rubles, tenge, dollars, euros; the language's currency is offered first), and gets a ready set of income and expense categories in that language. Amounts read as `1 500 000 сум` / `$1 250.50`, can be typed as `500к`, `200 тыс`, `1.5 млн`, `300 ming`; the balance also shows ≈ $ and €, Settings shows today's rates and changes the currency. Income offers only income categories, expenses only expense ones, with "➕ New category" right in the picker. `/backup` and e-mail reports stay owner-only.

✉️ **Feedback and error reports:** `/feedback` (or Settings → «✉️ Написать разработчику») sends text or a screenshot to the owner with a «↩️ Ответить» button; the owner's answer is delivered back. Unhandled errors give the user a short apology and the owner a notice with the error type and code location only (no message text or amounts), at most once per 10 minutes per error.

🏦 **Bank statements:** forward or paste payment notifications from bank bots (`➖ 37'000 UZS`, `🔹 merchant`, `🕘 21:48 23.10.2026`, `✅ Успешно`): the bot finds every payment, skips unsuccessful ones and those already recorded, and asks for a category one by one, offering the category last used for that merchant first; "📦 All the rest here" files the remaining ones at once. Operations keep the real payment time and the merchant as the comment; foreign currencies are converted by the rate of that day. `FEATURE_STATEMENTS=0` turns it off.

## How to get started

1. **Launching the bot**: After clicking on the `start` button, we are   greeted with a greeting and a button panel appears to control the bot.
![Start](https://github.com/KonstantinS343/Finance-telegram-bot/blob/main/images/start.gif)

2. **Create categories:**  Use the appropriate buttons to create/delete personalized categories.
![Categories](https://github.com/KonstantinS343/Finance-telegram-bot/blob/main/images/categoris.gif)

3. **Add operations:** Send the bot information about your income 💰 and expenses 💸 using the appropriate buttons. Specify the amount and select the appropriate category.
![Money](https://github.com/KonstantinS343/Finance-telegram-bot/blob/main/images/money.gif)

4. **Change the language:** Select a convenient interface language using the settings button (there is also reference information there). Now you can interact with the bot in your preferred language.
![Settings](https://github.com/KonstantinS343/Finance-telegram-bot/blob/main/images/settings.gif)

5. **Get reports:** Use the `/report` command to get reports on your finances. View data in the form of tables 📊 and pie charts 📈 to better manage your budget.
![Report](https://github.com/KonstantinS343/Finance-telegram-bot/blob/main/images/report.gif)

## Customization

### Environment

Copy 📜 `src/.env.example` to `src/.env` and fill it in. Every variable is described there; there are no default passwords.

- `OWNER_ID` is required: your numeric Telegram id (ask @userinfobot). The bot answers only this user; updates from anyone else are dropped without a reply. Without it the bot refuses to start.
- `REDIS_PASSWORD` is required by docker compose. Redis db 3 is the cache, db 4 keeps FSM dialog states, the Celery broker uses the db from `CELERY_PORT`.
- Generate passwords with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.

> If the Postgres volume already exists, changing `POSTGRES_PASSWORD` in `.env` does not change the database password. Change it inside the database (`ALTER USER ... PASSWORD ...`) and then update `.env` and `DB_URL`.

> Upgrading a database created by the old (username-based) bot: the migration needs `OWNER_ID`, and `OWNER_USERNAME` (your old Telegram username) when the database has more than one user. It copies the data, checks the row counts and is reversible with `alembic downgrade 8ca044247220`. Back up first: `docker compose exec db pg_dump -U <user> <db> > backup.sql`.

Windows quick start in Russian: [ЗАПУСК.md](ЗАПУСК.md) (`start.bat` / `stop.bat`).

### Security notes

- Postgres and Redis publish no ports; they are reachable only inside the compose network.
- pgAdmin is optional: `docker compose --profile tools up -d pgadmin`, then open `http://127.0.0.1:5050` (on a server use an SSH tunnel: `ssh -L 5050:127.0.0.1:5050 user@server`).
- Report files are created in `src/report/generated/<uuid>/` and deleted right after sending.
- Celery accepts JSON only; SQL statements are not logged.

### Preparation

For a local launch (outside docker) run this command:

```sh
export PYTHONPATH=path_to_project:$PYTHONPATH
```

Instead `path_to_project` use the path to the project.

### Launch

#### On-site launch

At the root of the project in 1 terminal:

```sh
python3.12 -m venv venv
source venv/bin/activate

python -m pip install -r requirements.txt

cd alembic/
alembic upgrade head

cd ..
python src/main.py
```

In the `src` folder in terminal 2:

```sh
celery -A celery_app:celery worker --loglevel=INFO 
```

#### In docker

Linux / Raspberry Pi 4 (64-bit): `./start.sh` (creates `src/.env` with random passwords on the first run), `./stop.sh`, `./restore.sh FILE`. Every dependency has an ARM64 build.


At the root of the project:

```docker
docker compose up -d --build
```

### Tests

```sh
python -m pip install -r requirements-dev.txt
flake8 ./
pytest
```

Tests feed updates through the real dispatcher with a mocked Telegram session, so they need no database, Redis or network.

## Tech used

- [Aiogram 3](https://docs.aiogram.dev/en/latest/) ⚡
- [Redis](https://redis.io/)  🚀
- [PostgreSQL](https://www.postgresql.org/)  🐘
- [SQLAlchemy](https://www.sqlalchemy.org/)  🧪
- [Celery](https://docs.celeryq.dev/en/stable/) 🖥
- [Alembic](https://alembic.sqlalchemy.org/en/latest/) ⚗️
- [Docker](https://www.docker.com/) 📦
- [Babel](https://babel.pocoo.org/en/latest/) 🌐

## License

This Telegram bot is open source and available under the [MIT License](LICENCE).
