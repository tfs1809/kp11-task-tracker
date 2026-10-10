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
    return render(request, 'workitems/roster.html', item_roster(request.user, item_id))
