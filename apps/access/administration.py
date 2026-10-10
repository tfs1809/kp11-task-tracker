import uuid

from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.audit.services import record_action

from .catalog import ADMIN_ONLY, PERMISSIONS
from .models import Role, RolePermission
from .policies import require_permission
from .services import actor_transaction, require_write_transaction


class VersionConflict(ValidationError):
    pass


def check_version(instance, expected):
    if instance.version != expected:
        raise VersionConflict("Данные изменились в другой сессии. Сверьте изменения и откройте форму заново.")


def ensure_administrator():
    require_write_transaction()
    exists = User.objects.filter(
        is_active=True, email_verified_at__isnull=False,
        user_roles__role__is_system_admin=True, user_roles__role__is_active=True,
    ).exclude(password__startswith="!").exclude(password="").exists()
    if not exists:
        raise ValidationError("Нельзя лишить доступа последнего действующего администратора.")


def save_role(*, actor_id, auth_version, role_id=None, expected_version=None, name, is_active, permission_codes):
    codes = set(permission_codes)
    if codes - PERMISSIONS.keys():
        raise ValidationError("Неизвестное разрешение.")
    with actor_transaction(actor_id, write=True, session_auth_version=auth_version) as actor:
        require_permission(actor, "access.manage_roles")
        role = Role.objects.get(pk=role_id) if role_id else Role(key=uuid.uuid4().hex)
        if role_id:
            check_version(role, expected_version)
        if role.is_system_admin:
            if codes != set(PERMISSIONS):
                raise ValidationError("Каталог прав системного администратора нельзя изменять.")
        elif codes & ADMIN_ONLY:
            raise ValidationError("Управление сотрудниками, ролями и группами разрешено только системной роли администратора.")
        before = {"name": role.name, "is_active": role.is_active, "permissions": list(role.permissions.values_list("code", flat=True))} if role_id else None
        role.name, role.is_active = name.strip(), is_active
        if role_id:
            role.version += 1
        role.full_clean()
        role.save()
        RolePermission.objects.filter(role=role).exclude(permission_id__in=codes).delete()
        for code in codes:
            RolePermission.objects.get_or_create(role=role, permission_id=code)
        ensure_administrator()
        record_action(actor=actor, action="access.role_saved", object_type="role", object_id=role.pk, changes={"before": before, "after": {"name": role.name, "is_active": role.is_active, "permissions": sorted(codes)}})
        return role
