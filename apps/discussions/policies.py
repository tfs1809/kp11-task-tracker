from apps.access.policies import has_permission
from apps.workitems.policies import can_edit, can_read


def can_send(actor, item):
    return can_read(actor, item) and item.kind == 'event' and not item.archived_at and not item.chat_closed_at and (
        item.author_id == actor.pk or item.assignments.filter(user=actor, state='active', user__is_active=True).exists()
        or has_permission(actor, 'discussions.send_accessible'))


def can_moderate(actor, item):
    return can_read(actor, item) and not item.archived_at and has_permission(actor, 'discussions.moderate')


def can_close(actor, item):
    return item.kind == 'event' and not item.chat_closed_at and can_edit(actor, item)


def can_read_original(actor):
    return has_permission(actor, 'audit.view') and actor.user_roles.filter(role__is_active=True, role__is_system_admin=True).exists()
