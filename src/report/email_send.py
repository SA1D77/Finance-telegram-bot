import re
import smtplib

from os.path import basename
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

from config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD
from celery_app import celery
from .files import ReportFiles, build_report_files, remove_report_files

# Deliberately simple: one "@", no spaces, a dot in the domain, RFC length limit.
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def is_valid_email(value) -> bool:
    return isinstance(value, str) and len(value) <= 254 and EMAIL_RE.match(value) is not None


def email_template(files: ReportFiles, user_email: str):
    msg = MIMEMultipart()
    msg['Subject'] = 'Отчет'
    msg['From'] = SMTP_USER
    msg['To'] = user_email

    with open(files.table, "rb") as f:
        part = MIMEApplication(f.read(), name=basename(files.table))
        msg.attach(part)

    for path in (files.income_diagram, files.expenditure_diagram, files.general_diagram):
        with open(path, 'rb') as image_file:
            image = MIMEImage(image_file.read(), name=basename(path))
            msg.attach(image)

    return msg


@celery.task
def email_report(user_email: str, accounts, lang):
    files = build_report_files(accounts=accounts, lang=lang or 'ru')
    try:
        email = email_template(files=files, user_email=user_email)
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as email_server:
            email_server.login(SMTP_USER, SMTP_PASSWORD)
            email_server.send_message(email)
    finally:
        remove_report_files(files)
