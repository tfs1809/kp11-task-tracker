from apps.access.models import Role
from apps.access.policies import has_permission
from django.core.exceptions import PermissionDenied
from apps.audiences.models import AudienceGroup

from .models import User


def administration_overview(actor):
    flags = {name: has_permission(actor, code) for name, code in [('can_manage_users', 'accounts.manage_users'), ('can_manage_roles', 'access.manage_roles'), ('can_manage_groups', 'audiences.manage_groups')]}
    if not any(flags.values()):
        raise PermissionDenied('Нет прав администрирования.')
    return {
        **flags,
        "employees": User.objects.order_by("full_name", "id").prefetch_related("user_roles__role", "memberships__group") if flags['can_manage_users'] else User.objects.none(),
        "roles": Role.objects.all() if flags['can_manage_roles'] else Role.objects.none(), "groups": AudienceGroup.objects.all() if flags['can_manage_groups'] else AudienceGroup.objects.none(),
    }


def own_access(actor):
    return {
        "own_roles": Role.objects.filter(user_roles__user=actor, is_active=True),
        "own_groups": AudienceGroup.objects.filter(memberships__user=actor, is_active=True),
        "can_administer": any(has_permission(actor, code) for code in ('accounts.manage_users', 'access.manage_roles', 'audiences.manage_groups')),
    }
