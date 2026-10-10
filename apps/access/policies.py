from django.core.exceptions import PermissionDenied


def require_active_actor(actor, session_auth_version=None):
    if not actor or not actor.is_authenticated or not actor.is_active or not actor.email_verified_at:
        raise PermissionDenied("Нет доступа.")
    if session_auth_version is not None and actor.auth_version != session_auth_version:
        raise PermissionDenied("Сессия отозвана.")
    return actor


def has_permission(actor, code, obj=None):
    from .models import RolePermission

    if not actor or not actor.is_authenticated or not actor.is_active or not actor.email_verified_at:
        return False
    if obj is not None:
        from apps.workitems.models import WorkItem
        from apps.workitems.policies import can_read, can_read_roster, can_edit, can_manage_assignees
        if not isinstance(obj, WorkItem):
            return False
        checks = {"workitems.read": can_read, "workitems.read_roster": can_read_roster, "workitems.edit": can_edit, "workitems.manage_assignees": can_manage_assignees}
        return checks[code](actor, obj) if code in checks else False
    grants = RolePermission.objects.filter(role__user_roles__user_id=actor.pk, role__is_active=True, permission_id=code)
    return grants.filter(permission__admin_only=False).exists() or grants.filter(role__is_system_admin=True).exists()


def require_permission(actor, code, obj=None):
    require_active_actor(actor)
    if not has_permission(actor, code, obj):
        raise PermissionDenied("Действие запрещено.")
