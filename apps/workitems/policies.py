from apps.access.policies import has_permission, require_permission

CREATE_CODES = ('workitems.create', 'workitems.view_own', 'workitems.manage_own_assignees')


def can_issue(actor):
    return all(has_permission(actor, code) for code in CREATE_CODES)


def require_issue(actor):
    for code in CREATE_CODES:
        require_permission(actor, code)


def can_read_roster(actor, item):
    return has_permission(actor, 'workitems.view_all') or (item.author_id == actor.pk and has_permission(actor, 'workitems.view_own'))


def can_read(actor, item):
    return can_read_roster(actor, item) or item.assignments.filter(user_id=actor.pk, user__is_active=True, state='active').exists()


def can_edit(actor, item):
    return can_read(actor, item) and item.archived_at is None and (has_permission(actor, 'workitems.edit_all') or (item.author_id == actor.pk and has_permission(actor, 'workitems.edit_own')))


def can_manage_assignees(actor, item):
    return can_read(actor, item) and item.archived_at is None and (has_permission(actor, 'workitems.edit_all') or (item.author_id == actor.pk and has_permission(actor, 'workitems.manage_own_assignees')))
