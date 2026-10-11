import os
import urllib.request

request = urllib.request.Request(
    "http://127.0.0.1:8000/health/ready/",
    headers={
        "X-Healthcheck-Key": os.environ["HEALTHCHECK_TOKEN"],
        "Host": os.environ.get("APP_ALLOWED_HOSTS", "localhost").split(",")[0].strip().lstrip("."),
    },
)
with urllib.request.urlopen(request, timeout=3) as response:
    if response.status != 200 or response.read() != b"ok\n":
        raise SystemExit(1)
