from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.access.administration import check_version, ensure_administrator
from apps.access.catalog import ADMIN_ONLY
from apps.access.models import Role, UserRole
from apps.access.policies import require_permission
from apps.access.services import actor_transaction
from apps.audiences.models import AudienceGroup, Membership
from apps.audit.services import record_action

from .models import Invitation, PasswordResetGrant, User


def resolve_choices(role_ids, group_ids):
    roles = list(Role.objects.filter(pk__in=role_ids))
    groups = list(AudienceGroup.objects.filter(pk__in=group_ids))
    if len(roles) != len(set(role_ids)) or len(groups) != len(set(group_ids)):
        raise ValidationError("Роль или группа не найдена.")
    return roles, groups


def set_memberships(user, roles, groups, actor):
    UserRole.objects.filter(user=user).exclude(role__in=roles).delete()
    Membership.objects.filter(user=user).exclude(group__in=groups).delete()
    for role in roles:
        UserRole.objects.get_or_create(user=user, role=role, defaults={"created_by": actor})
    for group in groups:
        Membership.objects.get_or_create(user=user, group=group, defaults={"created_by": actor})


def edit_employee(*, actor_id, auth_version, user_id, expected_version, full_name, is_active, role_ids, group_ids):
    with actor_transaction(actor_id, write=True, session_auth_version=auth_version) as actor:
        for code in ADMIN_ONLY:
            require_permission(actor, code)
        user = User.objects.get(pk=user_id)
        check_version(user, expected_version)
        roles, groups = resolve_choices(role_ids, group_ids)
        before = {"full_name": user.full_name, "is_active": user.is_active, "roles": list(map(str, user.user_roles.values_list("role_id", flat=True))), "groups": list(map(str, user.memberships.values_list("group_id", flat=True)))}
        user.full_name = full_name.strip()
        if user.is_active and not is_active:
            user.auth_version += 1
            now = timezone.now()
            Invitation.objects.filter(user=user, revoked_at__isnull=True, used_at__isnull=True).update(revoked_at=now)
            PasswordResetGrant.objects.filter(user=user, revoked_at__isnull=True, used_at__isnull=True).update(revoked_at=now)
        user.is_active = is_active
        user.version += 1
        user.full_clean()
        user.save()
        set_memberships(user, roles, groups, actor)
        ensure_administrator()
        after = {"full_name": user.full_name, "is_active": user.is_active, "roles": list(map(str, role_ids)), "groups": list(map(str, group_ids))}
        record_action(actor=actor, action="accounts.employee_changed", object_type="user", object_id=user.pk, changes={"before": before, "after": after})
        return user
