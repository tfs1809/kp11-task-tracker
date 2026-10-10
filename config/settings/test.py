from .migration import *  # noqa: F403

ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1", "web"]
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
EMAIL_USE_TLS = False
EMAIL_USE_SSL = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
DATABASES["default"]["TEST"] = {"NAME": "test_kp11"}  # noqa: F405
TEST_RUNNER = "core.testing.PostgresTestRunner"
