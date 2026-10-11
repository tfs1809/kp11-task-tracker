from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.access.administration import VersionConflict
from apps.access.services import actor_transaction
from apps.accounts.limits import RateLimited

from . import selectors, services
from .forms import CloseForm, HideForm, SendForm


def page(request, item_id, *, form=None, error='', status=200):
    with actor_transaction(request.user.pk, session_auth_version=request.session.get('auth_version')) as actor:
        context = selectors.history(actor, item_id, request.GET.get('before_id'))
        return render(request, 'discussions/chat.html', {**context, 'form': form or SendForm(), 'error': error}, status=status)


@require_GET
def chat(request, item_id):
    try:
        return page(request, item_id)
    except ValidationError:
        return invalid_history(request, item_id)


def invalid_history(request, item_id):
    context = selectors.history(request.user, item_id)
    return render(request, 'discussions/chat.html', {**context, 'form': SendForm(), 'error': 'Некорректный курсор истории.'}, status=400)


@require_GET
def changes(request, item_id):
    try:
        return JsonResponse(selectors.poll(request.user, item_id, request.GET.get('changes_after'), request.GET.get('visible', '')))
    except ValidationError:
        return JsonResponse({'error': 'Некорректный курсор.'}, status=400)
    except Http404:
        return JsonResponse({'error': 'Доступ к чату прекращен.'}, status=404)


def mutation(request, item_id, form, operation, *, sending=False):
    wants_json = request.headers.get('Accept') == 'application/json'
    retry_after = None
    try:
        if not form.is_valid():
            raise ValidationError('Проверьте заполнение формы.')
        result = operation(form.cleaned_data)
    except (ValidationError, RateLimited, PermissionDenied, Http404) as exc:
        if isinstance(exc, RateLimited):
            status, error, retry_after = 429, 'Слишком частая отправка. Подождите перед повтором.', exc.retry_after
        elif isinstance(exc, ValidationError):
            status, error = (409 if isinstance(exc, VersionConflict) else 400), ' '.join(exc.messages)
        elif isinstance(exc, PermissionDenied):
            status, error = 403, 'Действие запрещено.'
        else:
            status, error = 404, 'Доступ к чату прекращен.'
        if wants_json:
            # Invalid data cannot bypass object authorization either.
            with actor_transaction(request.user.pk, session_auth_version=request.session.get('auth_version')) as actor:
                selectors.event(actor, item_id)
                response = JsonResponse({'error': error}, status=status)
        else:
            response = page(request, item_id, form=form if sending else None, error=error, status=status)
        if retry_after:
            response['Retry-After'] = str(retry_after)
        return response
    if wants_json and sending:
        message, created = result
        # Recheck after commit, never serialize a stale service result containing text.
        with actor_transaction(request.user.pk, session_auth_version=request.session.get('auth_version')) as actor:
            item = selectors.event(actor, item_id)
            data = selectors.serialize(selectors.messages(item).filter(pk=message.pk), item)[0]
            return JsonResponse({'message': data, 'created': created}, status=201 if created else 200)
    return redirect('event_chat', item_id=item_id)


@require_POST
def send(request, item_id):
    return mutation(request, item_id, SendForm(request.POST), lambda values: services.send(actor_id=request.user.pk, auth_version=request.session.get('auth_version'), item_id=item_id, **values), sending=True)


@require_POST
def hide(request, item_id, message_id):
    return mutation(request, item_id, HideForm(request.POST), lambda values: services.hide(actor_id=request.user.pk, auth_version=request.session.get('auth_version'), item_id=item_id, message_id=message_id, **values))


@require_POST
def close(request, item_id):
    return mutation(request, item_id, CloseForm(request.POST), lambda values: services.close(actor_id=request.user.pk, auth_version=request.session.get('auth_version'), item_id=item_id, expected_version=values['version']))


@require_GET
def original(request, item_id, message_id):
    item, message = selectors.original(request.user, item_id, message_id)
    return render(request, 'discussions/original.html', {'item': item, 'message': message})
