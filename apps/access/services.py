from contextlib import contextmanager
from contextvars import ContextVar

from django.db import connection, transaction

from apps.accounts.models import User

from .models import AccessGuard
from .policies import require_active_actor

_guard_mode = ContextVar("access_guard_mode", default=None)


def require_write_transaction():
    if not connection.in_atomic_block or _guard_mode.get() != "write":
        raise RuntimeError("Изменение требует транзакцию с write-блокировкой AccessGuard.")


@contextmanager
def guarded_transaction(*, write=False):
    """Guard должен захватываться раньше чтения пользователя и объектных связей."""
    previous_mode = _guard_mode.get()
    if write and previous_mode == "read":
        raise RuntimeError("Нельзя повышать shared lock до write lock; начните write-транзакцию сразу.")
    with transaction.atomic():
        if write:
            AccessGuard.objects.select_for_update().get(pk=1)
        else:
            with connection.cursor() as cursor:
                cursor.execute("SELECT id FROM access_accessguard WHERE id = 1 FOR SHARE")
                if cursor.fetchone() is None:
                    raise AccessGuard.DoesNotExist("AccessGuard не создан миграцией.")
        token = _guard_mode.set("write" if write or previous_mode == "write" else "read")
        try:
            yield
        finally:
            _guard_mode.reset(token)


@contextmanager
def actor_transaction(actor_id, *, write=False, session_auth_version=None):
    with guarded_transaction(write=write):
        actor = User.objects.get(pk=actor_id)
        require_active_actor(actor, session_auth_version)
        yield actor
