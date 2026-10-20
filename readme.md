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
