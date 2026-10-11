"""Фильтры строятся только поверх доступных пользователю объектов."""
from datetime import timedelta
from django import forms
from django.core.paginator import Paginator
from django.db.models import Case, Count, DateTimeField, F, Q, When
from django.utils import timezone

from apps.accounts.models import User
from .selectors import own_assignments, issued_items, all_items, archived_items
from .state import assignment_completed, assignment_can_restore, item_sort_value


class ListFilters(forms.Form):
    search = forms.CharField(label='Поиск', required=False, max_length=255, widget=forms.TextInput(attrs={'placeholder': 'Поиск'}))
    author = forms.ChoiceField(label='Автор', required=False)
    kind = forms.ChoiceField(label='Вид', required=False, choices=[('', 'Все виды'), ('task', 'Задача'), ('event', 'Событие')])
    sort = forms.ChoiceField(label='Сортировать', choices=[('due', 'По возрастанию'), ('-due', 'По убыванию')])

    def __init__(self, data, *, authors):
        super().__init__(data)
        self.fields['author'].choices = [('', 'Все авторы'), *[(str(pk), name) for pk, name in authors]]


def filtered(base, params, *, personal=False):
    prefix = 'work_item__' if personal else ''
    authors = User.objects.filter(pk__in=base.values(prefix + 'author_id')).order_by('full_name', 'pk').values_list('pk', 'full_name')
    data = params.copy()
    if 'sort' not in data:
        data['sort'] = 'due'
    form = ListFilters(data, authors=authors)
    if not form.is_valid():
        return base.none(), form
    if form.cleaned_data['search']:
        base = base.filter(**{prefix + 'title__icontains': form.cleaned_data['search'].strip()})
    for name in ('author', 'kind'):
        if form.cleaned_data[name]:
            base = base.filter(**{prefix + ('author_id' if name == 'author' else name): form.cleaned_data[name]})
    sort_value = Case(
        When(**{prefix + 'kind': 'event'}, then=F(prefix + 'event_at')),
        default=F(prefix + 'due_at'),
        output_field=DateTimeField(),
    )
    direction = '-interface_sort_at' if form.cleaned_data['sort'].startswith('-') else 'interface_sort_at'
    return base.annotate(interface_sort_at=sort_value).order_by(direction, 'pk'), form


def page_context(queryset, params):
    page = Paginator(queryset, 20).get_page(params.get('page'))
    query = params.copy()
    query.pop('page', None)
    return {'page_obj': page, 'pagination_query': query.urlencode()}


def personal_list(actor, params):
    base = own_assignments(actor)
    rows, form = filtered(base, params, personal=True)
    tab = params.get('tab', 'assigned')
    valid_tab = tab in ('assigned', 'completed')
    if not valid_tab:
        form.add_error(None, 'Выберите вкладку «Назначены» или «Выполнены».')
        rows = rows.none()
    now = timezone.now()
    expired_event = Q(work_item__kind='event', work_item__event_at__lte=now - timedelta(days=1))
    effective_completed = Q(completed_at__isnull=False) | expired_event
    counts = rows.aggregate(assigned=Count('pk', filter=~effective_completed), completed=Count('pk', filter=effective_completed))
    rows = rows.filter(~effective_completed if tab != 'completed' else effective_completed).annotate(
        subtask_count=Count('progress', filter=Q(progress__subtask__deleted_at__isnull=True)),
        checked_count=Count('progress', filter=Q(progress__subtask__deleted_at__isnull=True, progress__checked_at__isnull=False, progress__checked_revision=F('progress__subtask__content_revision'))),
    )
    context = page_context(rows, params)
    for assignment in context['page_obj']:
        assignment.effective_completed = assignment_completed(assignment, now)
        assignment.can_restore = assignment_can_restore(assignment, now)
        assignment.overdue = not assignment.effective_completed and assignment.work_item.due_at < now
        assignment.sort_at = item_sort_value(assignment.work_item)
        progress = {row.subtask_id: row for row in assignment.progress.select_related('subtask').all()}
        assignment.interface_subtasks = []
        for subtask in assignment.work_item.subtasks.filter(deleted_at__isnull=True):
            mark = progress.get(subtask.pk)
            assignment.interface_subtasks.append({'subtask': subtask, 'progress': mark, 'checked': bool(mark and mark.checked_at and mark.checked_revision == subtask.content_revision)})
        assignment.all_checked = assignment.checked_count == assignment.subtask_count
    links = {}
    for name in ('assigned', 'completed'):
        query = params.copy()
        query.pop('page', None)
        query['tab'] = name
        links[name] = '?' + query.urlencode()
    return {**context, 'filters': form, 'personal': True, 'tab': tab, 'tab_links': links, 'counts': counts, 'assignments': context['page_obj'], 'status': 200 if form.is_valid() and valid_tab else 400}


def item_list(actor, params, scope):
    base = {'issued': issued_items, 'all': all_items, 'archive': archived_items}[scope](actor)
    if scope != 'archive':
        base = base.filter(archived_at__isnull=True)
    rows, form = filtered(base, params)
    if scope != 'archive':
        active = Q(assignments__state='active', assignments__user__is_active=True)
        rows = rows.annotate(active_count=Count('assignments', filter=active), completed_count=Count('assignments', filter=active & Q(assignments__completed_at__isnull=False)))
    context = page_context(rows, params)
    for item in context['page_obj']:
        item.overdue = not item.archived_at and item.due_at < timezone.now()
    return {**context, 'filters': form, 'personal': False, 'items': context['page_obj'], 'scope': scope, 'status': 200 if form.is_valid() else 400}
