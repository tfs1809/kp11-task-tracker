from apps.access.services import require_write_transaction

from .models import AuditEntry

# Расширяется вместе с проверенными services следующих этапов.
ALLOWED_CHANGES = {"environment.probe_created": {"marker"}}


def record_action(*, actor, action, object_type, object_id, changes=None, reason=""):
    require_write_transaction()
    changes = changes or {}
    allowed = ALLOWED_CHANGES.get(action)
    if allowed is None or set(changes) - allowed:
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
