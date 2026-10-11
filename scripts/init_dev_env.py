"""Создать локальные случайные секреты, не выводя и не перезаписывая их."""
import os
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / ".env"
if path.exists():
    print(".env уже существует; сохранен без изменений.")
else:
    content = (ROOT / ".env.example").read_text()
    for name in ("APP_SECRET_KEY", "HEALTHCHECK_TOKEN", "POSTGRES_PASSWORD", "DB_APP_PASSWORD", "DB_MIGRATOR_PASSWORD"):
        content = content.replace(f"{name}=\n", f"{name}={secrets.token_urlsafe(48)}\n")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(content)
    print("Создан development .env (0600). Секреты не выводятся.")
