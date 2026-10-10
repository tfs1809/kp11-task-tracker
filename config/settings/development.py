from .base import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = csv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,web")  # noqa: F405
EMAIL_HOST = os.environ.get("EMAIL_HOST", "mailpit")  # noqa: F405
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "1025"))  # noqa: F405
EMAIL_USE_TLS = False
EMAIL_USE_SSL = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
