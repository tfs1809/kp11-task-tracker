from apps.access.services import require_write_transaction

from .models import AuditEntry

# Расширяется вместе с проверенными services следующих этапов.
ALLOWED_CHANGES = {"environment.probe_created": {"marker"}}
ALLOWED_CHANGES.update({
    "workitems.mark_set": {"assignment_id", "subtask_id", "subtask_text", "before", "after"},
    **{action: {"assignment_id", "previous_completed_at", "completed_at", "completion_epoch"} for action in ["workitems.completed", "workitems.returned"]},
    "workitems.edited": {"before", "after", "impact", "progress_epoch"},
    "workitems.assignment_revoked": {"assignment_id", "user_id"},
    **{action: {"assignment_id", "user_id", "selection_id", "restoration", "assigned_at"} for action in ["workitems.assignment_added", "workitems.assignment_restored"]},
    "workitems.archived": {"archived_at"},
    "workitems.issued": {"kind", "selection_id", "recipients", "subtasks", "due_at", "event_at"},
    "accounts.bootstrap_started": {"email", "full_name"},
    "accounts.employee_invited": {"email", "full_name", "roles", "groups"},
    "accounts.employee_changed": {"before", "after"},
    "accounts.invitation_sent": {"invitation_id", "expires_at"},
    "access.role_saved": {"before", "after"},
    "audiences.group_saved": {"before", "after"},
    **{action: set() for action in ["accounts.activated", "accounts.login", "accounts.logout", "accounts.recovery_requested", "accounts.password_reset", "accounts.password_changed"]},
})


def record_action(*, actor, action, object_type, object_id, changes=None, reason=""):
    require_write_transaction()
    changes = changes or {}
    allowed = ALLOWED_CHANGES.get(action)
    def sensitive(value):
        if isinstance(value, dict):
            return any(key.lower() in {"password", "token", "token_digest", "cookie", "secret", "authorization", "message_text"} or sensitive(item) for key, item in value.items())
        return isinstance(value, (list, tuple)) and any(sensitive(item) for item in value)

    if allowed is None or set(changes) - allowed or sensitive(changes):
        raise ValueError("Действие или поля не разрешены каталогом аудита.")
    return AuditEntry.objects.create(
        actor=actor,
        actor_label=str(actor) if actor else "Система",
        action=action,
        object_type=object_type,
        object_id=str(object_id),
        changes=changes,
        reason=reason,
    )
