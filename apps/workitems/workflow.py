"""Изменения прогресса и выданных заданий: AccessGuard, версии, аудит."""
from datetime import datetime

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.access.administration import VersionConflict, check_version
from apps.access.services import actor_transaction, require_write_transaction
from apps.audit.services import record_action

from .models import Assignment, AssignmentSource, AudienceSelection, AudienceSelectionGroup, AudienceSelectionRole, AudienceSelectionUser, Subtask, SubtaskProgress
from .policies import can_edit, can_manage_assignees
from .selectors import readable_items
from .services import canonical_payload, resolve_recipients

SALT = 'workitems.workflow.preview.v1'


class ConsequencesChanged(VersionConflict):
    def __init__(self, preview):
        self.preview = preview
        super().__init__('Последствия изменились или подтверждение истекло. Проверьте актуальные последствия и подтвердите еще раз.')


def managed_item(actor, item_id, action):
    item = get_object_or_404(readable_items(actor), pk=item_id)
    allowed = can_edit(actor, item) if action in ('edit', 'archive') else can_manage_assignees(actor, item)
    if not allowed:
        raise PermissionDenied('Действие запрещено или задание архивировано.')
    return item


def current_subtasks(item):
    return list(item.subtasks.filter(deleted_at__isnull=True))


def checked(progress, subtask):
    return progress is not None and progress.checked_at is not None and progress.checked_revision == subtask.content_revision


def all_checked(assignment, subtasks):
    progress = {row.subtask_id: row for row in assignment.progress.all()}
    return all(checked(progress.get(subtask.pk), subtask) for subtask in subtasks)


def initialize_progress(assignment):
    require_write_transaction()
    present = set(assignment.progress.values_list('subtask_id', flat=True))
    SubtaskProgress.objects.bulk_create([SubtaskProgress(work_item_id=assignment.work_item_id, assignment=assignment, subtask=subtask) for subtask in current_subtasks(assignment.work_item) if subtask.pk not in present])


def own_assignment(actor, assignment_id):
    assignment = get_object_or_404(Assignment.objects.select_related('work_item'), pk=assignment_id, user_id=actor.pk, state='active', user__is_active=True)
    if assignment.work_item.archived_at:
        raise PermissionDenied('Архив доступен только для чтения.')
    return assignment


def set_mark(*, actor_id, auth_version, assignment_id, subtask_id, expected_version, expected_item_version, expected_progress_version, value):
    if not isinstance(value, bool):
        raise ValidationError('Отметка должна быть true или false.')
    with actor_transaction(actor_id, write=True, session_auth_version=auth_version) as actor:
        assignment = own_assignment(actor, assignment_id)
        check_version(assignment.work_item, expected_item_version)
        check_version(assignment, expected_version)
        if assignment.completed_at:
            raise ValidationError('Сначала верните выполненное назначение.')
        subtask = get_object_or_404(Subtask, pk=subtask_id, work_item_id=assignment.work_item_id, deleted_at__isnull=True)
        progress = get_object_or_404(SubtaskProgress, assignment=assignment, subtask=subtask)
        check_version(progress, expected_progress_version)
        if checked(progress, subtask) == value:
            return assignment
        before = {'checked_at': progress.checked_at.isoformat() if progress.checked_at else None, 'checked_revision': progress.checked_revision}
        progress.checked_at = timezone.now() if value else None
        progress.checked_revision = subtask.content_revision if value else None
        progress.version += 1
        progress.save()
        assignment.version += 1
        assignment.save(update_fields=['version'])
        record_action(actor=actor, action='workitems.mark_set', object_type='work_item', object_id=assignment.work_item_id, changes={'assignment_id': str(assignment.pk), 'subtask_id': str(subtask.pk), 'subtask_text': subtask.text, 'before': before, 'after': {'checked_at': progress.checked_at.isoformat() if progress.checked_at else None, 'checked_revision': progress.checked_revision}})
        return assignment


def set_completion(*, actor_id, auth_version, assignment_id, expected_version, expected_item_version, completed):
    if not isinstance(completed, bool):
        raise ValidationError('Некорректное действие.')
    with actor_transaction(actor_id, write=True, session_auth_version=auth_version) as actor:
        assignment = own_assignment(actor, assignment_id)
        check_version(assignment.work_item, expected_item_version)
        check_version(assignment, expected_version)
        if (assignment.completed_at is not None) == completed:
            return assignment
        if completed and not all_checked(assignment, current_subtasks(assignment.work_item)):
            raise ValidationError('Отметьте все текущие подзадачи.')
        previous = assignment.completed_at.isoformat() if assignment.completed_at else None
        assignment.completed_at = timezone.now() if completed else None
        assignment.completion_epoch = assignment.work_item.progress_epoch if completed else None
        assignment.version += 1
        assignment.save(update_fields=['completed_at', 'completion_epoch', 'version'])
        record_action(actor=actor, action='workitems.completed' if completed else 'workitems.returned', object_type='work_item', object_id=assignment.work_item_id, changes={'assignment_id': str(assignment.pk), 'previous_completed_at': previous, 'completed_at': assignment.completed_at.isoformat() if assignment.completed_at else None, 'completion_epoch': assignment.completion_epoch})
        return assignment


def edit_payload(item, payload):
    import uuid
    if not isinstance(payload, dict) or set(payload) != {'title', 'description', 'due_at', 'event_at', 'subtasks'} or not isinstance(payload['subtasks'], list):
        raise ValidationError('Некорректные поля редактирования.')
    entries = []
    for row in payload['subtasks']:
        if not isinstance(row, dict) or set(row) != {'id', 'text'}:
            raise ValidationError('Некорректная подзадача.')
        try:
            pk = str(uuid.UUID(str(row['id']))) if row['id'] else None
        except (ValueError, TypeError):
            raise ValidationError('Некорректный ID подзадачи.') from None
        entries.append({'id': pk, 'text': row['text']})
    common = canonical_payload({**payload, 'kind': item.kind, 'subtasks': [row['text'] for row in entries], 'users': [], 'roles': [], 'groups': []})
    for row, text in zip(entries, common['subtasks']):
        row['text'] = text
    identifiers = [row['id'] for row in entries if row['id']]
    if len(identifiers) != len(set(identifiers)) or set(identifiers) - set(str(row.pk) for row in current_subtasks(item)):
        raise ValidationError('Подзадача повторена, удалена или относится к другому заданию.')
    return {name: common[name] for name in ('title', 'description', 'due_at', 'event_at')} | {'subtasks': entries}


def content_snapshot(item):
    return {'title': item.title, 'description': item.description, 'due_at': item.due_at.isoformat(), 'event_at': item.event_at.isoformat() if item.event_at else None, 'subtasks': [{'id': str(row.pk), 'text': row.text, 'position': row.position, 'revision': row.content_revision} for row in current_subtasks(item)]}


def edit_impact(item, payload):
    existing = {str(row.pk): row for row in current_subtasks(item)}
    changed = [row['id'] for row in payload['subtasks'] if row['id'] and existing[row['id']].text != row['text']]
    added = sum(row['id'] is None for row in payload['subtasks'])
    deleted = sorted(set(existing) - {row['id'] for row in payload['subtasks']})
    reset = bool(changed or added)
    affected = [{'id': str(row.pk), 'name': row.user.full_name, 'version': row.version, 'completed_at': row.completed_at.isoformat() if row.completed_at else None} for row in item.assignments.filter(state='active', user__is_active=True).select_related('user').order_by('id')] if reset else []
    return {'reset': reset, 'changed': changed, 'added': added, 'deleted': deleted, 'affected': affected, 'completed_count': sum(row['completed_at'] is not None for row in affected)}


def make_preview(actor, item, operation, payload, impact):
    document = {'actor': str(actor.pk), 'item': str(item.pk), 'operation': operation, 'version': item.version, 'payload': payload, 'impact': impact}
    return {**document, 'token': signing.dumps(document, salt=SALT, compress=True)}


def preview_edit(*, actor_id, auth_version, item_id, expected_version, payload):
    with actor_transaction(actor_id, session_auth_version=auth_version) as actor:
        item = managed_item(actor, item_id, 'edit')
        check_version(item, expected_version)
        payload = edit_payload(item, payload)
        return make_preview(actor, item, 'edit', payload, edit_impact(item, payload))


def load_confirmation(token, actor, item, operation):
    try:
        document = signing.loads(token, salt=SALT)
        if document['actor'] != str(actor.pk) or document['item'] != str(item.pk) or document['operation'] != operation:
            raise ValueError
    except (signing.BadSignature, TypeError, KeyError, ValueError):
        raise ValidationError('Некорректное подтверждение.') from None
    return document


def verify_impact(token, document, actor, item, payload, impact):
    check_version(item, document['version'])
    try:
        signing.loads(token, salt=SALT, max_age=600)
    except signing.SignatureExpired:
        raise ConsequencesChanged(make_preview(actor, item, document['operation'], payload, impact)) from None
    if document['impact'] != impact:
        raise ConsequencesChanged(make_preview(actor, item, document['operation'], payload, impact))


def apply_edit(*, actor_id, auth_version, item_id, token):
    with actor_transaction(actor_id, write=True, session_auth_version=auth_version) as actor:
        item = managed_item(actor, item_id, 'edit')
        document = load_confirmation(token, actor, item, 'edit')
        check_version(item, document['version'])
        payload = edit_payload(item, document['payload'])
        impact = edit_impact(item, payload)
        verify_impact(token, document, actor, item, payload, impact)
        before = content_snapshot(item)
        if before['title'] == payload['title'] and before['description'] == payload['description'] and before['due_at'] == payload['due_at'] and before['event_at'] == payload['event_at'] and [{'id': row['id'], 'text': row['text']} for row in before['subtasks']] == payload['subtasks']:
            return item
        now = timezone.now()
        existing = {str(row.pk): row for row in current_subtasks(item)}
        # Освободить частичный UNIQUE перед перестановкой; финальные строки снова active.
        item.subtasks.filter(deleted_at__isnull=True).update(deleted_at=now)
        changed_ids = []
        for position, row in enumerate(payload['subtasks'], 1):
            if row['id']:
                subtask = existing[row['id']]
                if subtask.text != row['text']:
                    subtask.content_revision += 1
                    changed_ids.append(subtask.pk)
                subtask.text, subtask.position, subtask.deleted_at = row['text'], position, None
                subtask.save(update_fields=['text', 'position', 'content_revision', 'deleted_at'])
            else:
                subtask = Subtask.objects.create(work_item=item, text=row['text'], position=position)
                changed_ids.append(subtask.pk)
        active = item.assignments.filter(state='active', user__is_active=True)
        if impact['reset']:
            item.progress_epoch += 1
            active.update(completed_at=None, completion_epoch=None, version=F('version') + 1)
            SubtaskProgress.objects.filter(assignment__in=active, subtask_id__in=changed_ids).update(checked_at=None, checked_revision=None, version=F('version') + 1)
            for assignment in active:
                initialize_progress(assignment)
        item.title, item.description = payload['title'], payload['description']
        item.due_at = datetime.fromisoformat(payload['due_at'])
        item.event_at = datetime.fromisoformat(payload['event_at']) if payload['event_at'] else None
        item.version += 1
        item.save(update_fields=['title', 'description', 'due_at', 'event_at', 'version', 'progress_epoch'])
        record_action(actor=actor, action='workitems.edited', object_type='work_item', object_id=item.pk, changes={'before': before, 'after': content_snapshot(item), 'impact': impact, 'progress_epoch': item.progress_epoch})
        return item


def revoke_row(actor, assignment, reason):
    require_write_transaction()
    assignment.state = 'revoked'
    assignment.revoked_at, assignment.revoked_by, assignment.revoked_reason = timezone.now(), actor, reason
    assignment.version += 1
    assignment.save(update_fields=['state', 'revoked_at', 'revoked_by', 'revoked_reason', 'version'])
    record_action(actor=actor, action='workitems.assignment_revoked', object_type='work_item', object_id=assignment.work_item_id, changes={'assignment_id': str(assignment.pk), 'user_id': str(assignment.user_id)}, reason=reason)


def revoke_assignment(*, actor_id, auth_version, assignment_id, expected_version, expected_item_version, reason):
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
        raise ValidationError('Укажите причину отзыва (до 500 символов).')
    with actor_transaction(actor_id, write=True, session_auth_version=auth_version) as actor:
        assignment = get_object_or_404(Assignment, pk=assignment_id)
        item = managed_item(actor, assignment.work_item_id, 'assignees')
        check_version(item, expected_item_version)
        check_version(assignment, expected_version)
        if assignment.state != 'active':
            return item
        revoke_row(actor, assignment, reason.strip())
        item.version += 1
        item.save(update_fields=['version'])
        return item


def revoke_employee_assignments(actor, user):
    require_write_transaction()
    from .models import WorkItem
    changed = set()
    for assignment in user.assignments.filter(state='active').order_by('work_item_id', 'id'):
        revoke_row(actor, assignment, 'Отключение сотрудника')
        changed.add(assignment.work_item_id)
    WorkItem.objects.filter(pk__in=changed).update(version=F('version') + 1)


def restoration(assignment, item):
    subtasks = current_subtasks(item)
    return {'id': str(assignment.pk), 'version': assignment.version, 'state': assignment.state, 'restores_completed': bool(assignment.completed_at and assignment.completion_epoch == item.progress_epoch and all_checked(assignment, subtasks)), 'checked_count': sum(checked(row, row.subtask) for row in assignment.progress.filter(subtask__deleted_at__isnull=True).select_related('subtask')), 'previous_completed_at': assignment.completed_at.isoformat() if assignment.completed_at else None}


def addition_impact(item, payload):
    snapshot = resolve_recipients(payload)
    existing = {str(row.user_id): row for row in item.assignments.all()}
    for recipient in snapshot['recipients']:
        row = existing.get(recipient['id'])
        recipient['previous'] = restoration(row, item) if row else None
    return snapshot


def canonical_audience(payload):
    import uuid
    if not isinstance(payload, dict) or set(payload) != {'users', 'roles', 'groups'}:
        raise ValidationError('Некорректные адресаты.')
    try:
        if any(not isinstance(values, list) or len(values) > 10000 for values in payload.values()):
            raise ValueError
        return {name: sorted(set(str(uuid.UUID(str(pk))) for pk in values)) for name, values in payload.items()}
    except (ValueError, TypeError):
        raise ValidationError('Некорректные адресаты.') from None


def preview_additions(*, actor_id, auth_version, item_id, expected_version, payload):
    payload = canonical_audience(payload)
    with actor_transaction(actor_id, session_auth_version=auth_version) as actor:
        item = managed_item(actor, item_id, 'assignees')
        check_version(item, expected_version)
        return make_preview(actor, item, 'add', payload, addition_impact(item, payload))


def apply_additions(*, actor_id, auth_version, item_id, token):
    with actor_transaction(actor_id, write=True, session_auth_version=auth_version) as actor:
        item = managed_item(actor, item_id, 'assignees')
        document = load_confirmation(token, actor, item, 'add')
        check_version(item, document['version'])
        payload = canonical_audience(document['payload'])
        impact = addition_impact(item, payload)
        verify_impact(token, document, actor, item, payload, impact)
        targets = [row for row in impact['recipients'] if row['previous'] is None or row['previous']['state'] == 'revoked']
        if not targets:
            return item
        now = timezone.now()
        selection = AudienceSelection.objects.create(work_item=item, selected_by=actor, selected_at=now)
        sources = {}
        for name, model, field in [('users', AudienceSelectionUser, 'user_id'), ('roles', AudienceSelectionRole, 'role_id'), ('groups', AudienceSelectionGroup, 'group_id')]:
            for pk, label in impact['selected'][name].items():
                sources[name, pk] = model.objects.create(selection=selection, label_snapshot=label, **{field: pk})
        for recipient in targets:
            if recipient['previous']:
                assignment = Assignment.objects.get(pk=recipient['previous']['id'])
                initialize_progress(assignment)
                for progress in assignment.progress.filter(subtask__deleted_at__isnull=True).select_related('subtask'):
                    if progress.checked_at and progress.checked_revision != progress.subtask.content_revision:
                        progress.checked_at = progress.checked_revision = None
                        progress.version += 1
                        progress.save(update_fields=['checked_at', 'checked_revision', 'version'])
                if not recipient['previous']['restores_completed']:
                    assignment.completed_at = assignment.completion_epoch = None
                assignment.state, assignment.revoked_at, assignment.revoked_by, assignment.revoked_reason = 'active', None, None, ''
                assignment.assigned_at, assignment.assigned_by = now, actor
                assignment.version += 1
                assignment.save()
                action = 'workitems.assignment_restored'
            else:
                assignment = Assignment.objects.create(work_item=item, user_id=recipient['id'], assigned_by=actor, assigned_at=now, first_assigned_at=now)
                initialize_progress(assignment)
                action = 'workitems.assignment_added'
            for name, pk in recipient['sources']:
                field = {'users': 'selected_user', 'roles': 'selected_role', 'groups': 'selected_group'}[name]
                AssignmentSource.objects.create(assignment=assignment, batch_id=selection.batch_id, **{field: sources[name, pk]})
            record_action(actor=actor, action=action, object_type='work_item', object_id=item.pk, changes={'assignment_id': str(assignment.pk), 'user_id': str(assignment.user_id), 'selection_id': str(selection.pk), 'restoration': recipient['previous'], 'assigned_at': now.isoformat()})
        item.version += 1
        item.save(update_fields=['version'])
        return item


def archive_item(*, actor_id, auth_version, item_id, expected_version):
    with actor_transaction(actor_id, write=True, session_auth_version=auth_version) as actor:
        item = managed_item(actor, item_id, 'archive')
        check_version(item, expected_version)
        item.archived_at, item.archived_by = timezone.now(), actor
        item.version += 1
        item.save(update_fields=['archived_at', 'archived_by', 'version'])
        record_action(actor=actor, action='workitems.archived', object_type='work_item', object_id=item.pk, changes={'archived_at': item.archived_at.isoformat()})
        return item
