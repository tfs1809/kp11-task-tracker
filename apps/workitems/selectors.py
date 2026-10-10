from django.core.exceptions import PermissionDenied
from decimal import Decimal, ROUND_HALF_UP
from django.db.models import Q, Count
from django.utils import timezone
from django.shortcuts import get_object_or_404

from apps.access.models import Role
from apps.access.policies import has_permission, require_active_actor, require_permission
from apps.accounts.models import User
from apps.audiences.models import AudienceGroup

from .models import Assignment, WorkItem
from .policies import can_read_roster, can_edit, can_manage_assignees, require_issue


def recipient_directory(actor, item=None):
    if item is None:
        require_issue(actor)
    elif not can_manage_assignees(actor, item):
        raise PermissionDenied('Нет права управления исполнителями.')
    return {
        'users': list(User.objects.filter(is_active=True, invited_at__isnull=False).order_by('full_name', 'id').values_list('id', 'full_name')),
        'roles': list(Role.objects.filter(is_active=True).values_list('id', 'name')),
        'groups': list(AudienceGroup.objects.filter(is_active=True).values_list('id', 'name')),
    }


def readable_items(actor):
    require_active_actor(actor)
    if has_permission(actor, 'workitems.view_all'):
        return WorkItem.objects.all()
    basis = Q(assignments__user_id=actor.pk, assignments__state='active', assignments__user__is_active=True)
    if has_permission(actor, 'workitems.view_own'):
        basis |= Q(author_id=actor.pk)
    return WorkItem.objects.filter(basis).distinct()


def own_assignments(actor):
    require_active_actor(actor)
    return Assignment.objects.filter(user_id=actor.pk, state='active', user__is_active=True, work_item__archived_at__isnull=True).select_related('work_item', 'work_item__author').order_by('work_item__due_at', 'id')


def issued_items(actor):
    require_permission(actor, 'workitems.view_own')
    return WorkItem.objects.filter(author_id=actor.pk).select_related('author')


def all_items(actor):
    require_permission(actor, 'workitems.view_all')
    return WorkItem.objects.select_related('author').all()


def progress_rows(assignment, subtasks):
    from .workflow import checked
    progress = {row.subtask_id: row for row in assignment.progress.all()}
    return [{'subtask': row, 'progress': progress.get(row.pk), 'checked': checked(progress.get(row.pk), row)} for row in subtasks]


def statistics(item):
    values = item.assignments.filter(state='active', user__is_active=True).aggregate(total=Count('pk'), completed=Count('pk', filter=Q(completed_at__isnull=False)))
    values['percent'] = int((Decimal(values['completed']) * 100 / values['total']).quantize(Decimal('1'), rounding=ROUND_HALF_UP)) if values['total'] else None
    return values


def item_detail(actor, item_id):
    item = get_object_or_404(readable_items(actor).select_related('author'), pk=item_id)
    own = item.assignments.filter(user_id=actor.pk, state='active', user__is_active=True).first()
    subtasks = list(item.subtasks.filter(deleted_at__isnull=True))
    roster = can_read_roster(actor, item)
    rows = progress_rows(own, subtasks) if own else []
    return {'item': item, 'subtasks': subtasks, 'own_assignment': own, 'progress_rows': rows, 'all_checked': all(row['checked'] for row in rows), 'can_read_roster': roster, 'statistics': statistics(item) if roster else None, 'can_edit': can_edit(actor, item), 'can_manage': can_manage_assignees(actor, item), 'overdue': bool(own and not own.completed_at and not item.archived_at and item.due_at < timezone.now())}


def item_roster(actor, item_id, *, incomplete=False, history=False):
    detail = item_detail(actor, item_id)
    if not detail['can_read_roster']:
        raise PermissionDenied('Список исполнителей недоступен.')
    rows = detail['item'].assignments.all() if history else detail['item'].assignments.filter(state='active', user__is_active=True)
    if incomplete:
        rows = rows.filter(completed_at__isnull=True)
    assignments = list(rows.order_by('user__full_name', 'user_id').values('id', 'user_id', 'user__full_name', 'assigned_at', 'completed_at', 'state', 'version', 'revoked_at', 'revoked_reason', 'user__is_active'))
    return {**detail, 'assignments': assignments, 'history_mode': history, 'incomplete': incomplete}


def assignment_detail(actor, item_id, assignment_id):
    detail = item_detail(actor, item_id)
    assignment = get_object_or_404(Assignment.objects.select_related('user'), pk=assignment_id, work_item_id=item_id)
    if not detail['can_read_roster'] and (assignment.user_id != actor.pk or assignment.state != 'active'):
        from django.http import Http404
        raise Http404
    return {**detail, 'assignment': assignment, 'assignment_progress': progress_rows(assignment, detail['subtasks'])}


def item_history(actor, item_id):
    from apps.audit.models import AuditEntry
    detail = item_detail(actor, item_id)
    entries = AuditEntry.objects.filter(object_type='work_item', object_id=str(item_id)).order_by('-id')
    if not detail['can_read_roster']:
        own = detail['own_assignment']
        entries = entries.filter(Q(changes__assignment_id=str(own.pk)) | Q(action__in=['workitems.issued', 'workitems.edited', 'workitems.archived']))
    result = []
    names = {str(pk): name for pk, name in detail['item'].assignments.values_list('pk', 'user__full_name')} if detail['can_read_roster'] else {}
    def date(value):
        return timezone.datetime.fromisoformat(value) if value else None
    def revision(value):
        return {'title': value['title'], 'description': value['description'], 'due_at': date(value['due_at']), 'event_at': date(value['event_at']), 'subtasks': [row['text'] for row in value['subtasks']]}
    for entry in entries:
        changes = entry.changes if detail['can_read_roster'] else {key: value for key, value in entry.changes.items() if key in {'assignment_id', 'subtask_id', 'subtask_text', 'completed_at', 'previous_completed_at', 'archived_at', 'before', 'after'} and (key not in {'before', 'after'} or entry.action == 'workitems.mark_set')}
        labels = {'workitems.issued': 'Задание выдано', 'workitems.edited': 'Содержание изменено', 'workitems.mark_set': 'Отметка подзадачи изменена', 'workitems.completed': 'Назначение выполнено', 'workitems.returned': 'Назначение возвращено', 'workitems.assignment_added': 'Исполнитель добавлен', 'workitems.assignment_restored': 'Назначение восстановлено', 'workitems.assignment_revoked': 'Назначение отозвано', 'workitems.archived': 'Задание архивировано'}
        display = {'employee': names.get(changes.get('assignment_id')), 'subtask_text': changes.get('subtask_text'), 'mark_action': ('Отмечено' if changes.get('after', {}).get('checked_at') else 'Отметка снята') if entry.action == 'workitems.mark_set' else None, 'completed_at': date(changes.get('completed_at')), 'previous_completed_at': date(changes.get('previous_completed_at')), 'revision_before': revision(changes['before']) if entry.action == 'workitems.edited' and changes else None, 'revision_after': revision(changes['after']) if entry.action == 'workitems.edited' and changes else None, 'restoration': changes.get('restoration')}
        result.append({**display, 'action': labels.get(entry.action, 'Изменение задания'), 'actor': entry.actor_label, 'at': entry.occurred_at, 'changes': changes, 'reason': entry.reason})
    return {**detail, 'entries': result}


def archived_items(actor):
    return readable_items(actor).filter(archived_at__isnull=False).select_related('author')
