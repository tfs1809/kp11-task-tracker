from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.access.services import actor_transaction

from .forms import ConfirmationForm, IssueForm
from .policies import require_issue
from .selectors import all_items, issued_items, item_detail, item_roster, recipient_directory
from .services import IssueConflict, PreviewChanged, issue_item, preview_issue, read_preview


def confirmation_page(request, preview, *, status=200, error=''):
    with actor_transaction(request.user.pk, session_auth_version=request.session.get('auth_version')) as actor:
        require_issue(actor)
        form = ConfirmationForm(initial={'preview': preview['token'], 'request_key': preview['request_key']})
        return render(request, 'workitems/preview.html', {'preview': preview, 'form': form, 'error': error}, status=status)


@require_http_methods(['GET', 'POST'])
def issue(request):
    with actor_transaction(request.user.pk, session_auth_version=request.session.get('auth_version')) as actor:
        directory = recipient_directory(actor)
        form = IssueForm(request.POST if request.method == 'POST' else None, directory=directory)
        if request.method == 'POST' and form.is_valid():
            try:
                preview = preview_issue(actor_id=actor.pk, auth_version=actor.auth_version, request_key=form.cleaned_data['request_key'], payload=form.payload())
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                return confirmation_page(request, preview)
        return render(request, 'workitems/issue.html', {'form': form}, status=400 if request.method == 'POST' else 200)


@require_POST
def confirm(request):
    form = ConfirmationForm(request.POST)
    if not form.is_valid():
        return HttpResponse('Некорректное подтверждение. Вернитесь к форме.', status=400, content_type='text/plain; charset=utf-8')
    try:
        document = read_preview(form.cleaned_data['preview'])
        item, created = issue_item(actor_id=request.user.pk, auth_version=request.session.get('auth_version'), request_key=form.cleaned_data['request_key'], payload=document['payload'], preview_token=form.cleaned_data['preview'])
    except PreviewChanged as exc:
        return confirmation_page(request, exc.preview, status=409, error=exc.messages[0])
    except ValidationError as exc:
        # Повторная проверка доступа перед показом ошибки POST.
        with actor_transaction(request.user.pk, session_auth_version=request.session.get('auth_version')) as actor:
            require_issue(actor)
            return render(request, 'workitems/conflict.html', {'error': ' '.join(exc.messages)}, status=409 if isinstance(exc, IssueConflict) else 400)
    return redirect('workitem_detail', item_id=item.pk)


@require_GET
def issued(request):
    return render(request, 'workitems/list.html', {'heading': 'Выданные', 'items': issued_items(request.user)})


@require_GET
def all_workitems(request):
    return render(request, 'workitems/list.html', {'heading': 'Все задания', 'items': all_items(request.user)})


@require_GET
def detail(request, item_id):
    return render(request, 'workitems/detail.html', item_detail(request.user, item_id))


@require_GET
def roster(request, item_id):
    return render(request, 'workitems/roster.html', item_roster(request.user, item_id, incomplete=request.GET.get('incomplete') == '1', history=request.GET.get('history') == '1'))

# Этап 4: POST передает только ожидаемые версии и запрошенное действие.
from django.utils import timezone
from apps.access.administration import VersionConflict
from . import workflow
from .forms import AddAssigneesForm, ArchiveForm, CompletionForm, EditContentForm, MarkForm, RevokeForm, SubtaskEditFormSet, WorkflowConfirmationForm, MOSCOW
from .selectors import archived_items, assignment_detail, item_history


def workflow_page(request, preview, *, error='', status=200):
    with actor_transaction(request.user.pk, session_auth_version=request.session.get('auth_version')) as actor:
        workflow.managed_item(actor, preview['item'], preview['operation'])
        dates = {'due_at': timezone.datetime.fromisoformat(preview['payload']['due_at']), 'event_at': timezone.datetime.fromisoformat(preview['payload']['event_at']) if preview['payload']['event_at'] else None} if preview['operation'] == 'edit' else {}
        return render(request, 'workitems/workflow_preview.html', {**dates, 'preview': preview, 'form': WorkflowConfirmationForm(initial={'token': preview['token']}), 'error': error}, status=status)


@require_POST
def mark(request, assignment_id, subtask_id):
    form = MarkForm(request.POST)
    return progress_post(request, assignment_id, form, lambda values: workflow.set_mark(actor_id=request.user.pk, auth_version=request.session.get('auth_version'), assignment_id=assignment_id, subtask_id=subtask_id, expected_version=values['version'], expected_item_version=values['item_version'], expected_progress_version=values['progress_version'], value=values['value']))


@require_POST
def completion(request, assignment_id):
    form = CompletionForm(request.POST)
    return progress_post(request, assignment_id, form, lambda values: workflow.set_completion(actor_id=request.user.pk, auth_version=request.session.get('auth_version'), assignment_id=assignment_id, expected_version=values['version'], expected_item_version=values['item_version'], completed=values['completed']))


def progress_post(request, assignment_id, form, operation):
    from django.shortcuts import get_object_or_404
    from .models import Assignment
    try:
        if not form.is_valid():
            raise ValidationError('Некорректная форма действия.')
        assignment = operation(form.cleaned_data)
    except ValidationError as exc:
        with actor_transaction(request.user.pk, session_auth_version=request.session.get('auth_version')) as actor:
            assignment = get_object_or_404(Assignment, pk=assignment_id, user=actor, state='active')
            context = item_detail(actor, assignment.work_item_id)
            return render(request, 'workitems/detail.html', {**context, 'error': ' '.join(exc.messages)}, status=409 if isinstance(exc, VersionConflict) else 400)
    return redirect('workitem_detail', item_id=assignment.work_item_id)


def edit_initial(item, payload=None):
    if payload is None:
        payload = workflow.content_snapshot(item)
    initial = {'title': payload['title'], 'description': payload['description'], 'due_date': timezone.localtime(timezone.datetime.fromisoformat(payload['due_at']), MOSCOW).date(), 'event_at': timezone.datetime.fromisoformat(payload['event_at']) if payload['event_at'] else None, 'version': item.version}
    rows = [{'id': row['id'], 'text': row['text'], 'position': position} for position, row in enumerate(payload['subtasks'], 1)]
    return initial, rows


@require_http_methods(['GET', 'POST'])
def edit(request, item_id):
    with actor_transaction(request.user.pk, session_auth_version=request.session.get('auth_version')) as actor:
        item = workflow.managed_item(actor, item_id, 'edit')
        initial, rows = edit_initial(item)
        form = EditContentForm(request.POST if request.method == 'POST' else None, kind=item.kind, initial=initial)
        formset = SubtaskEditFormSet(request.POST if request.method == 'POST' else None, initial=rows, prefix='subtasks')
        status = 200
        if request.method == 'POST':
            status = 400
            valid_form, valid_rows = form.is_valid(), formset.is_valid()
            if valid_form and valid_rows:
                try:
                    entries = []
                    positions = []
                    for row_form in formset:
                        row = row_form.cleaned_data
                        if row and not row.get('delete') and row.get('text'):
                            if not row.get('position'):
                                raise ValidationError('Укажите порядок каждой подзадачи.')
                            positions.append(row['position'])
                            entries.append((row['position'], {'id': str(row['id']) if row.get('id') else None, 'text': row['text']}))
                        elif row.get('id') and not row.get('delete'):
                            raise ValidationError('Текст существующей подзадачи обязателен; для удаления отметьте «Убрать».')
                    if len(positions) != len(set(positions)):
                        raise ValidationError('Позиции подзадач не должны повторяться.')
                    payload = form.payload([entry for _, entry in sorted(entries, key=lambda entry: entry[0])])
                    preview = workflow.preview_edit(actor_id=actor.pk, auth_version=actor.auth_version, item_id=item_id, expected_version=form.cleaned_data['version'], payload=payload)
                except ValidationError as exc:
                    form.add_error(None, exc)
                    status = 409 if isinstance(exc, VersionConflict) else 400
                    if isinstance(exc, VersionConflict):
                        data = form.data.copy()
                        data['version'] = item.version
                        form = EditContentForm(data, kind=item.kind)
                        form.is_valid()
                        form.add_error(None, 'Задание изменилось. Ввод сохранен; сравните с актуальной карточкой и заново покажите последствия перед сохранением.')
                else:
                    return workflow_page(request, preview)
        return render(request, 'workitems/edit.html', {'item': item, 'form': form, 'formset': formset}, status=status)


@require_http_methods(['GET', 'POST'])
def add_assignees(request, item_id):
    with actor_transaction(request.user.pk, session_auth_version=request.session.get('auth_version')) as actor:
        item = workflow.managed_item(actor, item_id, 'assignees')
        form = AddAssigneesForm(request.POST if request.method == 'POST' else None, directory=recipient_directory(actor, item), initial={'version': item.version})
        status = 200
        if request.method == 'POST':
            status = 400
            if form.is_valid():
                try:
                    payload = {name: list(form.cleaned_data[name]) for name in ('users', 'roles', 'groups')}
                    preview = workflow.preview_additions(actor_id=actor.pk, auth_version=actor.auth_version, item_id=item_id, expected_version=form.cleaned_data['version'], payload=payload)
                except ValidationError as exc:
                    form.add_error(None, exc)
                    status = 409 if isinstance(exc, VersionConflict) else 400
                    if isinstance(exc, VersionConflict):
                        data = form.data.copy()
                        data['version'] = item.version
                        form = AddAssigneesForm(data, directory=recipient_directory(actor, item))
                        form.is_valid()
                        form.add_error(None, 'Задание изменилось. Выбор сохранен; заново проверьте получателей и восстановление.')
                else:
                    return workflow_page(request, preview)
        return render(request, 'workitems/add.html', {'item': item, 'form': form}, status=status)


@require_POST
def workflow_confirm(request, item_id):
    form = WorkflowConfirmationForm(request.POST)
    if not form.is_valid():
        return HttpResponse('Некорректное подтверждение.', status=400)
    token = form.cleaned_data['token']
    document = None
    try:
        with actor_transaction(request.user.pk, session_auth_version=request.session.get('auth_version')) as actor:
            from django.core import signing
            try:
                document = signing.loads(token, salt=workflow.SALT)
            except signing.BadSignature:
                raise ValidationError('Поврежденное подтверждение.') from None
            # Только известные операции, права повторно проверяются write-service.
            operation = document.get('operation')
            if operation not in ('edit', 'add'):
                raise ValidationError('Неизвестная операция.')
        service = workflow.apply_edit if operation == 'edit' else workflow.apply_additions
        item = service(actor_id=request.user.pk, auth_version=request.session.get('auth_version'), item_id=item_id, token=token)
    except workflow.ConsequencesChanged as exc:
        return workflow_page(request, exc.preview, error=exc.messages[0], status=409)
    except ValidationError as exc:
        with actor_transaction(request.user.pk, session_auth_version=request.session.get('auth_version')) as actor:
            # Не показывать payload другого actor/item даже при валидной чужой подписи.
            item = workflow.managed_item(actor, item_id, 'edit' if document and document.get('operation') == 'edit' else 'assignees')
            safe_draft = document and document.get('actor') == str(actor.pk) and document.get('item') == str(item.pk)
            if safe_draft and document['operation'] == 'edit':
                initial, rows = edit_initial(item, document['payload'])
                form = EditContentForm(kind=item.kind, initial=initial)
                formset = SubtaskEditFormSet(initial=rows, prefix='subtasks')
                return render(request, 'workitems/edit.html', {'item': item, 'form': form, 'formset': formset, 'error': ' '.join(exc.messages) + ' Ввод сохранен. Сравните с актуальной карточкой и заново подтвердите последствия.'}, status=409 if isinstance(exc, VersionConflict) else 400)
            if safe_draft and document['operation'] == 'add':
                form = AddAssigneesForm(directory=recipient_directory(actor, item), initial={**document['payload'], 'version': item.version})
                return render(request, 'workitems/add.html', {'item': item, 'form': form, 'error': ' '.join(exc.messages) + ' Выбор сохранен; заново покажите получателей и восстановление.'}, status=409 if isinstance(exc, VersionConflict) else 400)
            return render(request, 'workitems/conflict.html', {'error': ' '.join(exc.messages)}, status=409 if isinstance(exc, VersionConflict) else 400)
    return redirect('workitem_detail', item_id=item.pk)


@require_POST
def revoke(request, assignment_id):
    from django.shortcuts import get_object_or_404
    from .models import Assignment
    form = RevokeForm(request.POST)
    try:
        if not form.is_valid():
            raise ValidationError('Укажите версию и причину отзыва.')
        item = workflow.revoke_assignment(actor_id=request.user.pk, auth_version=request.session.get('auth_version'), assignment_id=assignment_id, expected_version=form.cleaned_data['version'], expected_item_version=form.cleaned_data['item_version'], reason=form.cleaned_data['reason'])
    except ValidationError as exc:
        with actor_transaction(request.user.pk, session_auth_version=request.session.get('auth_version')) as actor:
            assignment = get_object_or_404(Assignment, pk=assignment_id)
            item = workflow.managed_item(actor, assignment.work_item_id, 'assignees')
            return render(request, 'workitems/conflict.html', {'error': ' '.join(exc.messages), 'draft_reason': request.POST.get('reason', '')}, status=409 if isinstance(exc, VersionConflict) else 400)
    return redirect('workitem_roster', item_id=item.pk)


@require_POST
def archive(request, item_id):
    form = ArchiveForm(request.POST)
    try:
        if not form.is_valid():
            raise ValidationError('Некорректная версия задания.')
        item = workflow.archive_item(actor_id=request.user.pk, auth_version=request.session.get('auth_version'), item_id=item_id, expected_version=form.cleaned_data['version'])
    except ValidationError as exc:
        with actor_transaction(request.user.pk, session_auth_version=request.session.get('auth_version')) as actor:
            workflow.managed_item(actor, item_id, 'archive')
            return render(request, 'workitems/conflict.html', {'error': ' '.join(exc.messages)}, status=409 if isinstance(exc, VersionConflict) else 400)
    return redirect('workitem_detail', item_id=item.pk)


@require_GET
def assignee_progress(request, item_id, assignment_id):
    return render(request, 'workitems/assignment.html', assignment_detail(request.user, item_id, assignment_id))


@require_GET
def history(request, item_id):
    return render(request, 'workitems/history.html', item_history(request.user, item_id))


@require_GET
def archives(request):
    return render(request, 'workitems/list.html', {'heading': 'Архив', 'items': archived_items(request.user)})
