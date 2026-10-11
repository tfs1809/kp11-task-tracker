import hashlib
import json
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.access.administration import VersionConflict
from apps.access.services import actor_transaction
from apps.accounts.limits import consume_limits
from apps.audit.services import record_action
from apps.workitems.models import MutationReceipt

from .models import EventMessage
from .policies import can_close, can_moderate, can_send
from .selectors import event


def send(*, actor_id, auth_version, item_id, request_key, text):
    # UUID and content canonicalization are also enforced for non-HTTP callers.
    try:
        request_key = uuid.UUID(str(request_key))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('Некорректный ключ отправки.') from None
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 4000 or '\x00' in text:
        raise ValidationError('Сообщение должно содержать от 1 до 4000 символов обычного текста.')
    text = text.strip()
    digest = hashlib.sha256(json.dumps([str(item_id), text], ensure_ascii=False).encode()).hexdigest()
    # Acquire AccessGuard first, then bucket locks. Auth services finish rate limiting
    # before acquiring the guard; no code holds a bucket lock while waiting for guard.
    # This keeps message + receipt + limits + audit atomic, including retry/no-op.
    with actor_transaction(actor_id, write=True, session_auth_version=auth_version) as actor:
        item = event(actor, item_id)
        receipt = MutationReceipt.objects.filter(actor=actor, operation='discussions.send', request_key=request_key).first()
        if receipt:
            if receipt.payload_digest != digest:
                raise VersionConflict('Этот ключ уже использован для другого сообщения.')
            return get_object_or_404(EventMessage.objects.defer('text'), pk=receipt.message_id, work_item=item), False
        if not can_send(actor, item):
            raise PermissionDenied('Отправка в этот чат запрещена.')
        consume_limits([('chat.actor-event', f'{actor.pk}|{item.pk}', 10, 60), ('chat.actor', str(actor.pk), 30, 60)])
        with connection.cursor() as cursor:
            cursor.execute("SELECT nextval(pg_get_serial_sequence('discussions_eventmessage', 'id'))")
            message_id = cursor.fetchone()[0]
        audit = record_action(actor=actor, action='discussions.sent', object_type='work_item', object_id=item.pk, changes={'message_id': str(message_id)})
        message = EventMessage.objects.create(id=message_id, work_item=item, author=actor, text=text, last_change=audit)
        MutationReceipt.objects.create(actor=actor, operation='discussions.send', request_key=request_key, payload_digest=digest, work_item=item, message=message)
        return message, True


def hide(*, actor_id, auth_version, item_id, message_id, reason):
    reason = reason.strip() if isinstance(reason, str) else ''
    if not 1 <= len(reason) <= 500 or '\x00' in reason:
        raise ValidationError('Укажите причину скрытия от 1 до 500 символов.')
    with actor_transaction(actor_id, write=True, session_auth_version=auth_version) as actor:
        item = event(actor, item_id)
        if not can_moderate(actor, item):
            raise PermissionDenied('Нет права модерации этого события.')
        message = get_object_or_404(EventMessage.objects.defer('text').select_for_update(), pk=message_id, work_item=item)
        if message.hidden_at:
            return message
        audit = record_action(actor=actor, action='discussions.hidden', object_type='work_item', object_id=item.pk, changes={'message_id': str(message.pk)}, reason=reason)
        message.hidden_at, message.hidden_by, message.hidden_reason, message.last_change = timezone.now(), actor, reason, audit
        message.save(update_fields=['hidden_at', 'hidden_by', 'hidden_reason', 'last_change'])
        return message


def close(*, actor_id, auth_version, item_id, expected_version):
    with actor_transaction(actor_id, write=True, session_auth_version=auth_version) as actor:
        item = event(actor, item_id)
        if not can_close(actor, item):
            raise PermissionDenied('Закрытие чата запрещено.')
        if item.version != expected_version:
            raise VersionConflict('Событие изменилось. Обновите страницу перед закрытием чата.')
        item.chat_closed_at, item.chat_closed_by = timezone.now(), actor
        item.version += 1
        item.save(update_fields=['chat_closed_at', 'chat_closed_by', 'version'])
        record_action(actor=actor, action='discussions.closed', object_type='work_item', object_id=item.pk, changes={'closed_at': item.chat_closed_at.isoformat()})
        return item
