FROM python:3.12-slim

WORKDIR /aiogram

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

COPY ./requirements.txt /aiogram/requirements.txt

RUN pip install --no-cache-dir --upgrade -r /aiogram/requirements.txt

COPY . .

ENV PYTHONPATH "${PYTHONPATH}:/aiogram"