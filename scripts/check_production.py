"""Проверить settings; SMTP/HTTPS здесь не подключаются и не тестируются."""
import os
from pathlib import Path
import subprocess
import sys

environment = os.environ.copy()
environment.update({
    "DJANGO_SETTINGS_MODULE": "config.settings.production",
    "DJANGO_ALLOWED_HOSTS": "tracker.corp.kp11.ru",
    "DJANGO_CSRF_TRUSTED_ORIGINS": "https://tracker.corp.kp11.ru",
    "DJANGO_DEBUG": "false",
    "EMAIL_HOST": "smtp.example.org",
    "EMAIL_PORT": "587",
    "PUBLIC_BASE_URL": "https://tracker.corp.kp11.ru",
})
result = subprocess.run(
    [sys.executable, "manage.py", "check", "--deploy", "--fail-level", "ERROR"],
    cwd=Path(__file__).resolve().parents[1], env=environment,
)
raise SystemExit(result.returncode)
