import hashlib
import json
import uuid
from datetime import datetime, timezone as dt_timezone

from django.core import signing
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.access.models import Role, UserRole
from apps.access.services import actor_transaction
from apps.accounts.models import User
from apps.audiences.models import AudienceGroup, Membership
from apps.audit.services import record_action

from .models import Assignment, AssignmentSource, AudienceSelection, AudienceSelectionGroup, AudienceSelectionRole, AudienceSelectionUser, MutationReceipt, Subtask, WorkItem
from .policies import can_read, require_issue

SALT = 'workitems.issue.preview.v1'
PREVIEW_SECONDS = 600


class IssueConflict(ValidationError):
    pass


class PreviewChanged(IssueConflict):
    def __init__(self, preview):
        self.preview = preview
        super().__init__('Список или названия адресатов изменились. Проверьте новое превью и подтвердите отправку еще раз.')


def canonical_payload(payload):
    """Валидация также для CLI/service; никакие extra поля не становятся моделями."""
    if not isinstance(payload, dict) or set(payload) != {'kind', 'title', 'description', 'due_at', 'event_at', 'subtasks', 'users', 'roles', 'groups'}:
        raise ValidationError('Некорректные поля задания.')
    data = dict(payload)
    if data['kind'] not in WorkItem.Kind.values:
        raise ValidationError('Некорректный вид задания.')
    for name, maximum, required in [('title', 255, True), ('description', 10000, False)]:
        if not isinstance(data[name], str) or len(data[name]) > maximum or (required and not data[name].strip()):
            raise ValidationError('Некорректный текст задания.')
        data[name] = data[name].strip()
    if not isinstance(data['subtasks'], list) or len(data['subtasks']) > 50 or any(not isinstance(line, str) or not line.strip() or len(line) > 1000 for line in data['subtasks']):
        raise ValidationError('Некорректные подзадачи.')
    data['subtasks'] = [line.strip() for line in data['subtasks']]
    try:
        for name in ('due_at', 'event_at'):
            if name == 'event_at' and data[name] is None:
                continue
            value = datetime.fromisoformat(data[name])
            if not timezone.is_aware(value):
                raise ValueError
            data[name] = value.astimezone(dt_timezone.utc).isoformat()
        if (data['kind'] == 'event') != (data['event_at'] is not None):
            raise ValueError
        for name in ('users', 'roles', 'groups'):
            if not isinstance(data[name], list) or len(data[name]) > 10000:
                raise ValueError
            data[name] = sorted(set(str(uuid.UUID(str(value))) for value in data[name]))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('Некорректная дата или адресаты.') from None
    return data


def digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def resolve_recipients(payload):
    selected = {}
    for name, model, label in [('users', User, 'full_name'), ('roles', Role, 'name'), ('groups', AudienceGroup, 'name')]:
        rows = model.objects.filter(pk__in=payload[name], is_active=True)
        if name == 'users':
            rows = rows.filter(invited_at__isnull=False)
        selected[name] = {str(pk): text for pk, text in rows.values_list('id', label)}
        if len(selected[name]) != len(payload[name]):
            raise ValidationError('Выбранные адресаты отключены, не приглашены или недоступны. Вернитесь к форме и выберите актуальных адресатов.')
    reasons = {pk: [('users', pk)] for pk in selected['users']}
    for name, queryset, target_field in [('roles', UserRole.objects.filter(role_id__in=payload['roles']), 'role_id'), ('groups', Membership.objects.filter(group_id__in=payload['groups']), 'group_id')]:
        for user_id, source_id in queryset.filter(user__is_active=True, user__invited_at__isnull=False).values_list('user_id', target_field):
            reasons.setdefault(str(user_id), []).append((name, str(source_id)))
    names = dict((str(pk), label) for pk, label in User.objects.filter(pk__in=reasons).values_list('id', 'full_name'))
    recipients = [{'id': pk, 'name': names[pk], 'sources': [list(source) for source in sorted(set(reasons[pk]))]} for pk in sorted(reasons)]
    recipients.sort(key=lambda row: (row['name'], row['id']))
    if not recipients:
        raise ValidationError('Нужен хотя бы один активный приглашенный получатель.')
    return {'selected': selected, 'recipients': recipients}


def signed_preview(actor, request_key, payload, snapshot):
    document = {'actor': str(actor.pk), 'key': str(request_key), 'payload': payload, 'snapshot': snapshot}
    return {**snapshot, 'token': signing.dumps(document, salt=SALT, compress=True), 'payload': payload, 'request_key': request_key, 'due_at': datetime.fromisoformat(payload['due_at']), 'event_at': datetime.fromisoformat(payload['event_at']) if payload['event_at'] else None}


def preview_issue(*, actor_id, auth_version, request_key, payload):
    payload = canonical_payload(payload)
    try:
        request_key = uuid.UUID(str(request_key))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('Некорректный ключ запроса.') from None
    with actor_transaction(actor_id, session_auth_version=auth_version) as actor:
        require_issue(actor)
        return signed_preview(actor, request_key, payload, resolve_recipients(payload))


def issue_item(*, actor_id, auth_version, request_key, payload, preview_token):
    payload = canonical_payload(payload)
    try:
        request_key = uuid.UUID(str(request_key))
        confirmed = signing.loads(preview_token, salt=SALT)
        if confirmed['actor'] != str(actor_id) or confirmed['key'] != str(request_key):
            raise ValueError
    except (signing.BadSignature, ValueError, KeyError, TypeError, AttributeError):
        raise ValidationError('Превью повреждено или относится к другой форме. Получите новое превью.') from None
    with actor_transaction(actor_id, write=True, session_auth_version=auth_version) as actor:
        require_issue(actor)
        receipt = MutationReceipt.objects.filter(actor=actor, operation='workitems.issue', request_key=request_key).select_related('work_item').first()
        if receipt:
            if receipt.payload_digest != digest(payload):
                raise IssueConflict('Этот ключ запроса уже использован с другим содержимым. Начните новую выдачу.')
            if confirmed['payload'] != payload:
                raise ValidationError('Содержание не совпадает с подписанным превью.')
            if not can_read(actor, receipt.work_item):
                from django.core.exceptions import PermissionDenied
                raise PermissionDenied('Нет доступа к результату.')
            return receipt.work_item, False
        if confirmed['payload'] != payload:
            raise ValidationError('Содержание не совпадает с подписанным превью.')
        current = resolve_recipients(payload)
        try:
            signing.loads(preview_token, salt=SALT, max_age=PREVIEW_SECONDS)
        except signing.SignatureExpired:
            raise PreviewChanged(signed_preview(actor, request_key, payload, current)) from None
        if current != confirmed['snapshot']:
            raise PreviewChanged(signed_preview(actor, request_key, payload, current))
        now = timezone.now()
        item = WorkItem.objects.create(kind=payload['kind'], title=payload['title'], description=payload['description'], author=actor, due_at=datetime.fromisoformat(payload['due_at']), event_at=datetime.fromisoformat(payload['event_at']) if payload['event_at'] else None, created_at=now)
        Subtask.objects.bulk_create([Subtask(work_item=item, text=text, position=index, created_at=now) for index, text in enumerate(payload['subtasks'], 1)])
        selection = AudienceSelection.objects.create(work_item=item, selected_by=actor, selected_at=now)
        source_rows = {}
        for name, model, field in [('users', AudienceSelectionUser, 'user_id'), ('roles', AudienceSelectionRole, 'role_id'), ('groups', AudienceSelectionGroup, 'group_id')]:
            for pk, label in current['selected'][name].items():
                source_rows[name, pk] = model.objects.create(selection=selection, label_snapshot=label, **{field: pk})
        for recipient in current['recipients']:
            assignment = Assignment.objects.create(work_item=item, user_id=recipient['id'], assigned_by=actor, first_assigned_at=now, assigned_at=now)
            from .workflow import initialize_progress
            initialize_progress(assignment)
            for name, pk in recipient['sources']:
                field = {'users': 'selected_user', 'roles': 'selected_role', 'groups': 'selected_group'}[name]
                AssignmentSource.objects.create(assignment=assignment, batch_id=selection.batch_id, **{field: source_rows[name, pk]})
        MutationReceipt.objects.create(actor=actor, operation='workitems.issue', request_key=request_key, payload_digest=digest(payload), work_item=item)
        record_action(actor=actor, action='workitems.issued', object_type='work_item', object_id=item.pk, changes={'kind': item.kind, 'selection_id': str(selection.pk), 'recipients': [row['id'] for row in current['recipients']], 'subtasks': [str(pk) for pk in item.subtasks.values_list('id', flat=True)], 'due_at': payload['due_at'], 'event_at': payload['event_at']})
        return item, True


def read_preview(token):
    """HTTP confirmation reads only the signed canonical content; no client ids."""
    try:
        return signing.loads(token, salt=SALT)
    except (signing.BadSignature, TypeError, ValueError):
        raise ValidationError('Превью повреждено. Вернитесь к форме.') from None
