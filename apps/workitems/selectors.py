from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404

from apps.access.models import Role
from apps.access.policies import has_permission, require_active_actor, require_permission
from apps.accounts.models import User
from apps.audiences.models import AudienceGroup

from .models import Assignment, WorkItem
from .policies import can_read_roster, require_issue


def recipient_directory(actor):
    require_issue(actor)
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


def item_detail(actor, item_id):
    item = get_object_or_404(readable_items(actor).select_related('author'), pk=item_id)
    own = item.assignments.filter(user_id=actor.pk, state='active', user__is_active=True).first()
    return {'item': item, 'subtasks': list(item.subtasks.filter(deleted_at__isnull=True)), 'own_assignment': own, 'can_read_roster': can_read_roster(actor, item)}


def item_roster(actor, item_id):
    detail = item_detail(actor, item_id)
    if not detail['can_read_roster']:
        raise PermissionDenied('Список исполнителей недоступен.')
    # Поля личного профиля и почта не входят в выборку.
    assignments = list(detail['item'].assignments.filter(state='active', user__is_active=True).order_by('user__full_name', 'user_id').values('id', 'user_id', 'user__full_name', 'assigned_at'))
    return {**detail, 'assignments': assignments}
