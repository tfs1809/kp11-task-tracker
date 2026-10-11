"""Send a development probe email without a framework command runner."""

import argparse
import os
import smtplib
from email.message import EmailMessage

parser = argparse.ArgumentParser()
parser.add_argument("--marker", required=True)
args = parser.parse_args()
message = EmailMessage()
message["Subject"] = f"KP11: проверка среды [{args.marker}]"
message["From"] = os.environ.get("DEFAULT_FROM_EMAIL", "tracker@kp11.ru")
message["To"] = "environment-check@kp11.ru"
message.set_content("FastAPI доставил тестовое письмо через SMTP.")
with smtplib.SMTP(os.environ.get("EMAIL_HOST", "mailpit"), int(os.environ.get("EMAIL_PORT", "1025")), timeout=10) as smtp:
    smtp.send_message(message)
print("SMTP probe sent")
