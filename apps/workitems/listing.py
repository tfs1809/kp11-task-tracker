"""Фильтры строятся только поверх доступных пользователю объектов."""
from django import forms
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.utils import timezone

from apps.accounts.models import User
from .selectors import own_assignments, issued_items, all_items, archived_items


class ListFilters(forms.Form):
    author = forms.ChoiceField(label='Автор', required=False)
    kind = forms.ChoiceField(label='Вид', required=False, choices=[('', 'Все виды'), ('task', 'Задача'), ('event', 'Событие')])
    sort = forms.ChoiceField(label='Сортировка по сроку', choices=[('due', 'Ближайшие сначала'), ('-due', 'Поздние сначала')])

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
    for name in ('author', 'kind'):
        if form.cleaned_data[name]:
            base = base.filter(**{prefix + ('author_id' if name == 'author' else name): form.cleaned_data[name]})
    order = ('-' if form.cleaned_data['sort'].startswith('-') else '') + prefix + 'due_at'
    return base.order_by(order, 'pk'), form


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
    counts = rows.aggregate(assigned=Count('pk', filter=Q(completed_at__isnull=True)), completed=Count('pk', filter=Q(completed_at__isnull=False)))
    rows = rows.filter(completed_at__isnull=tab != 'completed').annotate(
        subtask_count=Count('progress', filter=Q(progress__subtask__deleted_at__isnull=True)),
        checked_count=Count('progress', filter=Q(progress__subtask__deleted_at__isnull=True, progress__checked_at__isnull=False, progress__checked_revision=F('progress__subtask__content_revision'))),
    )
    context = page_context(rows, params)
    for assignment in context['page_obj']:
        assignment.overdue = not assignment.completed_at and assignment.work_item.due_at < timezone.now()
    links = {}
    for name in ('assigned', 'completed'):
        query = params.copy()
        query.pop('page', None)
        query['tab'] = name
        links[name] = '?' + query.urlencode()
    return {**context, 'filters': form, 'tab': tab, 'tab_links': links, 'counts': counts, 'assignments': context['page_obj'], 'status': 200 if form.is_valid() and valid_tab else 400}


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
    return {**context, 'filters': form, 'items': context['page_obj'], 'scope': scope, 'status': 200 if form.is_valid() else 400}
