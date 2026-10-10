import logging
import sys


class SafeErrorHandler(logging.Handler):
    """Не сериализовать request, URL, cookies, traceback или тела форм в журнал."""

    def emit(self, record):
        sys.stderr.write("Ошибка HTTP; подробности запроса не журналируются.\n")
