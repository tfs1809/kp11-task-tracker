from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False
if os.environ.get("DJANGO_DEBUG", "false").lower() not in {"0", "false", "no"}:  # noqa: F405
    raise ImproperlyConfigured("DEBUG запрещен в production.")
if len(SECRET_KEY) < 50 or SECRET_KEY.startswith("django-insecure-"):  # noqa: F405
    raise ImproperlyConfigured("Нужен стойкий production SECRET_KEY длиной не менее 50 символов.")
if len(HEALTHCHECK_TOKEN) < 32:  # noqa: F405
    raise ImproperlyConfigured("Нужен стойкий HEALTHCHECK_TOKEN.")
if not ALLOWED_HOSTS or any(host in {"*", "localhost", "127.0.0.1", "web"} for host in ALLOWED_HOSTS):  # noqa: F405
    raise ImproperlyConfigured("Нужны явные production ALLOWED_HOSTS.")
CSRF_TRUSTED_ORIGINS = csv("DJANGO_CSRF_TRUSTED_ORIGINS")  # noqa: F405
if not CSRF_TRUSTED_ORIGINS or any(not origin.startswith("https://") or "*" in origin for origin in CSRF_TRUSTED_ORIGINS):
    raise ImproperlyConfigured("Нужны явные HTTPS CSRF_TRUSTED_ORIGINS.")
EMAIL_HOST = required("EMAIL_HOST")  # noqa: F405
if EMAIL_HOST.lower() in {"mailpit", "localhost", "127.0.0.1", "0.0.0.0", "::1"} or EMAIL_PORT == 1025:  # noqa: F405
    raise ImproperlyConfigured("Тестовая почта запрещена в production.")
if "EMAIL_BACKEND" in os.environ and os.environ["EMAIL_BACKEND"] != EMAIL_BACKEND:  # noqa: F405
    raise ImproperlyConfigured("В production разрешен только SMTP email backend.")
EMAIL_USE_TLS = True
EMAIL_USE_SSL = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_REDIRECT_EXEMPT = [r"^health/(live|ready)/$"]
# Только доверенный proxy должен задавать этот header; прямой доступ к web закрыт.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False
