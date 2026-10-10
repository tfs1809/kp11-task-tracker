from .base import *  # noqa: F403

DATABASES["default"]["USER"] = required("DB_MIGRATOR_USER")  # noqa: F405
DATABASES["default"]["PASSWORD"] = required("DB_MIGRATOR_PASSWORD")  # noqa: F405
