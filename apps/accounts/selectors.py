from apps.access.models import Role
from apps.access.policies import has_permission, require_permission
from apps.audiences.models import AudienceGroup

from .models import User


def administration_overview(actor):
    require_permission(actor, "accounts.manage_users")
    return {
        "employees": User.objects.order_by("full_name", "id").prefetch_related("user_roles__role", "memberships__group"),
        "roles": Role.objects.all(), "groups": AudienceGroup.objects.all(),
    }


def own_access(actor):
    return {
        "own_roles": Role.objects.filter(user_roles__user=actor, is_active=True),
        "own_groups": AudienceGroup.objects.filter(memberships__user=actor, is_active=True),
        "can_administer": has_permission(actor, "accounts.manage_users"),
    }
