from django.core.exceptions import ValidationError
from django.db.models import Max, Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.access.policies import require_active_actor
from apps.audiences.models import Membership
from apps.workitems.selectors import readable_items

from .models import EventMessage
from .policies import can_close, can_moderate, can_read_original, can_send

PAGE_SIZE = 50


def event(actor, item_id):
    require_active_actor(actor)
    return get_object_or_404(readable_items(actor).filter(kind='event'), pk=item_id)


def integer(value, *, default=None):
    if value in (None, ''):
        return default
    # Bound both SQL bigint and hostile oversized strings.
    if len(str(value)) > 19 or not str(value).isascii() or not str(value).isdecimal():
        raise ValidationError('Некорректный курсор.')
    result = int(value)
    if result > 9223372036854775807:
        raise ValidationError('Некорректный курсор.')
    return result


def messages(item):
    # Hidden text is never fetched by an ordinary selector, including visible refresh.
    return EventMessage.objects.filter(work_item=item).defer('text').select_related('author').prefetch_related(
        Prefetch('author__memberships', queryset=Membership.objects.filter(group__is_active=True).select_related('group'), to_attr='chat_memberships'))


def serialize(rows, item):
    rows = list(rows)
    texts = dict(EventMessage.objects.filter(pk__in=[row.pk for row in rows if not row.hidden_at], hidden_at__isnull=True).values_list('pk', 'text'))
    return [dict(id=str(row.pk), author=row.author.full_name,
                 groups=[membership.group.name for membership in row.author.chat_memberships],
                 created_at=timezone.localtime(row.created_at).strftime('%d.%m.%Y %H:%M:%S'),
                 hidden=bool(row.hidden_at), text=texts.get(row.pk, '') if not row.hidden_at else '',
                 reason=row.hidden_reason if row.hidden_at else '', change_id=str(row.last_change_id)) for row in rows]


def state(actor, item):
    return dict(closed=bool(item.chat_closed_at), archived=bool(item.archived_at), can_send=can_send(actor, item), can_moderate=can_moderate(actor, item), can_close=can_close(actor, item), can_original=can_read_original(actor), version=item.version)


def history(actor, item_id, before=None):
    item = event(actor, item_id)
    before = integer(before)
    query = messages(item)
    if before is not None:
        query = query.filter(id__lt=before)
    rows = list(query.order_by('-id')[:PAGE_SIZE + 1])
    has_older = len(rows) > PAGE_SIZE
    rows = list(reversed(rows[:PAGE_SIZE]))
    watermark = item.messages.aggregate(value=Max('last_change_id'))['value'] or 0
    return dict(item=item, rows=serialize(rows, item), before_id=str(rows[0].pk) if has_older else '', cursor=str(watermark), state=state(actor, item))


def poll(actor, item_id, after, visible=''):
    item = event(actor, item_id)
    after = integer(after, default=0)
    ids = visible.split(',') if visible else []
    if len(ids) > PAGE_SIZE:
        raise ValidationError('Слишком много сообщений для обновления.')
    ids = [integer(value) for value in ids]
    query = messages(item)
    rows = list(query.filter(last_change_id__gt=after).order_by('last_change_id', 'id')[:PAGE_SIZE + 1])
    more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    return dict(messages=serialize(rows, item), refresh=serialize(query.filter(pk__in=ids), item),
                cursor=str(rows[-1].last_change_id if rows else after), more=more, state=state(actor, item))


def original(actor, item_id, message_id):
    from django.core.exceptions import PermissionDenied
    item = event(actor, item_id)
    if not can_read_original(actor):
        raise PermissionDenied('Оригинал доступен только администратору с правом аудита.')
    return item, get_object_or_404(EventMessage.objects.select_related('author', 'hidden_by'), work_item=item, pk=message_id, hidden_at__isnull=False)
