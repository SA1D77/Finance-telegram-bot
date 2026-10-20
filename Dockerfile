# bookworm ships postgresql-client 15, matching the postgres:15 service (pg_dump for backups).
FROM python:3.12-slim-bookworm

WORKDIR /aiogram

ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Optional offline recognisers (start.bat sets these from FEATURE_VOICE / FEATURE_RECEIPTS in src/.env).
ARG INSTALL_VOICE=0
ARG INSTALL_RECEIPTS=0
ARG VOSK_MODEL=vosk-model-small-ru-0.22

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && if [ "$INSTALL_VOICE" = "1" ]; then apt-get install -y --no-install-recommends ffmpeg unzip curl ca-certificates; fi \
    && if [ "$INSTALL_RECEIPTS" = "1" ]; then apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng; fi \
    && rm -rf /var/lib/apt/lists/*

COPY ./requirements.txt ./requirements-voice.txt /aiogram/

RUN pip install --no-cache-dir --upgrade -r /aiogram/requirements.txt \
    && if [ "$INSTALL_VOICE" = "1" ]; then pip install --no-cache-dir -r /aiogram/requirements-voice.txt; fi

# Russian speech model (~45 MB) from the Vosk project, unpacked to /opt/vosk-model.
RUN if [ "$INSTALL_VOICE" = "1" ]; then \
        curl -fsSL -o /tmp/model.zip "https://alphacephei.com/vosk/models/${VOSK_MODEL}.zip" \
        && unzip -q /tmp/model.zip -d /opt \
        && mv "/opt/${VOSK_MODEL}" /opt/vosk-model \
        && rm /tmp/model.zip; \
    fi

COPY . .

ENV PYTHONPATH "${PYTHONPATH}:/aiogram"
