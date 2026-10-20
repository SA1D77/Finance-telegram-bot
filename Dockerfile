# bookworm ships postgresql-client 15, matching the postgres:15 service (pg_dump for backups).
FROM python:3.12-slim-bookworm

WORKDIR /aiogram

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY ./requirements.txt /aiogram/requirements.txt

RUN pip install --no-cache-dir --upgrade -r /aiogram/requirements.txt

COPY . .

ENV PYTHONPATH "${PYTHONPATH}:/aiogram"
