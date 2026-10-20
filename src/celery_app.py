from celery import Celery

from config import celery_broker_url

celery = Celery('executor', broker=celery_broker_url(), include=['report.email_send', 'budgets.tasks'])

# JSON only: a pickle payload from the broker could run arbitrary code in the worker.
celery.conf.task_serializer = 'json'
celery.conf.result_serializer = 'json'
celery.conf.accept_content = ['json']
