from django.db import migrations
from django.db.models import F
from django.utils import timezone


def revoke_legacy(apps, schema_editor):
    """Этап 3 запрещал доступ disabled, но еще не отзывал строки Assignment."""
    Guard = apps.get_model('access', 'AccessGuard')
    Assignment = apps.get_model('workitems', 'Assignment')
    WorkItem = apps.get_model('workitems', 'WorkItem')
    Audit = apps.get_model('audit', 'AuditEntry')
    Guard.objects.select_for_update().get(pk=1)
    changed = set()
    now = timezone.now()
    for assignment in Assignment.objects.filter(state='active', user__is_active=False).order_by('work_item_id', 'id'):
        decision = Audit.objects.filter(action='accounts.employee_changed', object_type='user', object_id=str(assignment.user_id), changes__before__is_active=True, changes__after__is_active=False).order_by('-id').first()
        if decision is None or decision.actor_id is None:
            raise RuntimeError('Нельзя подтвердить прежнее отключение сотрудника по аудиту. Требуется восстановить согласованность данных до миграции.')
        reason = 'Миграция: назначение ранее отключенного сотрудника'
        assignment.state, assignment.revoked_at, assignment.revoked_by_id, assignment.revoked_reason = 'revoked', now, decision.actor_id, reason
        assignment.version += 1
        assignment.save(update_fields=['state', 'revoked_at', 'revoked_by', 'revoked_reason', 'version'])
        Audit.objects.create(actor_label='Система', action='workitems.assignment_revoked', object_type='work_item', object_id=str(assignment.work_item_id), changes={'assignment_id': str(assignment.pk), 'user_id': str(assignment.user_id)}, reason=reason)
        changed.add(assignment.work_item_id)
    WorkItem.objects.filter(pk__in=changed).update(version=F('version') + 1)


class Migration(migrations.Migration):
    dependencies = [('workitems', '0004_progress_integrity'), ('audit', '0002_append_only')]
    operations = [migrations.RunPython(revoke_legacy, migrations.RunPython.noop)]
