from celery import Celery

from celery.schedules import crontab

from config import BACKUP_HOUR, FEATURE_BACKUP, TIMEZONE, celery_broker_url

celery = Celery('executor', broker=celery_broker_url(), include=['report.email_send', 'budgets.tasks', 'recurring.tasks', 'backup.tasks'])

# JSON only: a pickle payload from the broker could run arbitrary code in the worker.
celery.conf.task_serializer = 'json'
celery.conf.result_serializer = 'json'
celery.conf.accept_content = ['json']

# Periodic jobs, run by the separate "beat" container (docker-compose.yaml).
celery.conf.timezone = TIMEZONE
celery.conf.beat_schedule = {
    'recurring-operations': {'task': 'recurring.tasks.process_recurring', 'schedule': 600.0},
}
if FEATURE_BACKUP:
    celery.conf.beat_schedule['database-backup'] = {
        'task': 'backup.tasks.backup_database', 'schedule': crontab(hour=BACKUP_HOUR, minute=0)}
