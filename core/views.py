from django.conf import settings
from django.db import DatabaseError, connection
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.crypto import constant_time_compare
from django.views.decorators.http import require_GET

from apps.access.selectors import own_profile
from apps.accounts.selectors import own_access
from apps.workitems.listing import personal_list

from .middleware import public_view


@require_GET
def home(request):
    context = personal_list(request.user, request.GET)
    return render(request, "core/home.html", {"profile": own_profile(request.user), **own_access(request.user), **context}, status=context['status'])


def health_allowed(request):
    return constant_time_compare(request.headers.get("X-Healthcheck-Key", ""), settings.HEALTHCHECK_TOKEN)


@public_view
@require_GET
def liveness(request):
    if not health_allowed(request):
        return HttpResponse(status=404)
    return HttpResponse("ok\n", content_type="text/plain")


@public_view
@require_GET
def readiness(request):
    if not health_allowed(request):
        return HttpResponse(status=404)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM access_accessguard WHERE id = 1")
            if cursor.fetchone() is None:
                return HttpResponse("unavailable\n", status=503, content_type="text/plain")
    except DatabaseError:
        return HttpResponse("unavailable\n", status=503, content_type="text/plain")
    return HttpResponse("ok\n", content_type="text/plain")


def error400(request, exception=None):
    return render(request, "errors/error.html", {"code": 400, "message": "Некорректный запрос."}, status=400)


def error403(request, exception=None):
    return render(request, "errors/error.html", {"code": 403, "message": "Нет доступа к этому действию."}, status=403)


def csrf_failure(request, reason=""):
    return render(request, "errors/error.html", {"code": 403, "message": "Проверка безопасности формы не пройдена. Обновите страницу."}, status=403)


def error404(request, exception=None):
    return render(request, "errors/error.html", {"code": 404, "message": "Страница не найдена."}, status=404)


def error500(request):
    # Ошибка БД не должна повторяться в auth/context processors страницы ошибки.
    return render(None, "errors/error.html", {"code": 500, "message": "Не удалось выполнить запрос. Попробуйте позже."}, status=500)
