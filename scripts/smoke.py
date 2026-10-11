"""Реальные HTTP/SMTP проверки текущего запуска Compose, без сотрудников и паролей."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import uuid

parser = argparse.ArgumentParser()
parser.add_argument("--project")
parser.add_argument("--web-port", type=int, default=8000)
parser.add_argument("--mailpit-port", type=int, default=8025)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
compose = ["docker", "compose"] + (["-p", args.project] if args.project else [])
web = f"http://127.0.0.1:{args.web_port}"
mailpit = f"http://127.0.0.1:{args.mailpit_port}"

with urllib.request.urlopen(web + "/", timeout=5) as response:
    body = response.read().decode()
    assert response.status == 200 and response.geturl().endswith("/accounts/login/?next=/")
    assert "Доступ только по приглашению администратора" in body
    assert response.headers["Cache-Control"] == "no-store"
with urllib.request.urlopen(web + "/static/css/base.css", timeout=5) as response:
    assert response.status == 200 and b"focus-visible" in response.read()
for endpoint in ["/health/live/", "/health/ready/"]:
    try:
        urllib.request.urlopen(web + endpoint, timeout=5)
        raise AssertionError("Health endpoint стал публичным.")
    except urllib.error.HTTPError as error:
        assert error.code == 404
subprocess.run(compose + ["exec", "-T", "web", "python", "scripts/check_health.py"], cwd=root, check=True)
print("HTTP: redirect/login, русский HTML, CSS, no-store, private health и DB readiness — OK")

marker = uuid.uuid4().hex
subprocess.run(compose + ["exec", "-T", "web", "python", "scripts/send_test_email.py", "--marker", marker], cwd=root, check=True)
subject = f"KP11: проверка среды [{marker}]"
message = None
for attempt in range(5):
    with urllib.request.urlopen(mailpit + "/api/v1/messages", timeout=5) as response:
        messages = json.load(response)["messages"]
    message = next((item for item in messages if item["Subject"] == subject), None)
    if message:
        break
    time.sleep(.2)
assert message is not None, "Письмо именно текущей проверки отсутствует в Mailpit."
with urllib.request.urlopen(mailpit + "/api/v1/message/" + message["ID"], timeout=5) as response:
    detail = json.load(response)
assert "FastAPI доставил тестовое письмо через SMTP." in detail["Text"]
print("SMTP: новое письмо текущей проверки получено Mailpit; текст совпал — OK")
