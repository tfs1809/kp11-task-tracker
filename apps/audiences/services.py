from apps.access.administration import check_version
from apps.access.policies import require_permission
from apps.access.services import actor_transaction
from apps.audit.services import record_action

from .models import AudienceGroup


def save_group(*, actor_id, auth_version, group_id=None, expected_version=None, name, is_active):
    with actor_transaction(actor_id, write=True, session_auth_version=auth_version) as actor:
        require_permission(actor, "audiences.manage_groups")
        group = AudienceGroup.objects.get(pk=group_id) if group_id else AudienceGroup()
        if group_id:
            check_version(group, expected_version)
        before = {"name": group.name, "is_active": group.is_active} if group_id else None
        group.name, group.is_active = name.strip(), is_active
        if group_id:
            group.version += 1
        group.full_clean()
        group.save()
        record_action(actor=actor, action="audiences.group_saved", object_type="audience_group", object_id=group.pk, changes={"before": before, "after": {"name": group.name, "is_active": group.is_active}})
        return group
