from celery import Celery

from config import TIMEZONE, celery_broker_url

celery = Celery('executor', broker=celery_broker_url(), include=['report.email_send', 'budgets.tasks', 'recurring.tasks'])

# JSON only: a pickle payload from the broker could run arbitrary code in the worker.
celery.conf.task_serializer = 'json'
celery.conf.result_serializer = 'json'
celery.conf.accept_content = ['json']

# Periodic jobs, run by the separate "beat" container (docker-compose.yaml).
celery.conf.timezone = TIMEZONE
celery.conf.beat_schedule = {
    'recurring-operations': {'task': 'recurring.tasks.process_recurring', 'schedule': 600.0},
}
