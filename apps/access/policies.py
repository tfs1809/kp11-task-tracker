from django.core.exceptions import PermissionDenied


def require_active_actor(actor, session_auth_version=None):
    if not actor or not actor.is_authenticated or not actor.is_active or not actor.email_verified_at:
        raise PermissionDenied("Нет доступа.")
    if session_auth_version is not None and actor.auth_version != session_auth_version:
        raise PermissionDenied("Сессия отозвана.")
    return actor


def has_permission(actor, code, obj=None):
    # Каталог и роли появятся на этапе 2. Не выдавать временные привилегии.
    return False


def require_permission(actor, code, obj=None):
    require_active_actor(actor)
    if not has_permission(actor, code, obj):
        raise PermissionDenied("Действие запрещено.")
