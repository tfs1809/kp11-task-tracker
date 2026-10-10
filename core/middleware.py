from django.contrib.auth import logout
from django.contrib.auth.views import redirect_to_login
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin

from apps.access.services import guarded_transaction


def public_view(view):
    view.public_access = True
    return view


class ProtectedAccessMiddleware(MiddlewareMixin):
    def process_request(self, request):
        # Проверять Host даже в views, которые строят только относительные URL.
        request.get_host()

    def process_view(self, request, view_func, view_args, view_kwargs):
        if getattr(view_func, "public_access", False):
            return None
        with guarded_transaction():
            actor = request.user
            valid = actor.is_authenticated and actor.is_active and actor.email_verified_at is not None
            if valid:
                valid = request.session.get("auth_version") == actor.auth_version
            if not valid:
                if actor.is_authenticated:
                    logout(request)
                if request.headers.get("Accept") == "application/json":
                    return JsonResponse({"error": "authentication_required"}, status=401)
                return redirect_to_login(request.get_full_path())
            if request.method in {"GET", "HEAD", "OPTIONS"}:
                return view_func(request, *view_args, **view_kwargs)
        # POST-сервисы сами берут write guard, заново читают actor и проверяют epoch.
        # Лимит запросов завершается до guard, чтобы не инвертировать порядок locks.
        return view_func(request, *view_args, **view_kwargs)

    def process_response(self, request, response):
        if response.status_code == 500:
            from .views import error500

            response = error500(request)
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "strict-origin"
        return response

    def process_exception(self, request, exception):
        # Даже DEBUG development не показывает auth-секреты через traceback page.
        import logging
        from django.core.exceptions import BadRequest, PermissionDenied, SuspiciousOperation
        from django.http import Http404
        from .views import error500

        if isinstance(exception, (PermissionDenied, Http404, BadRequest, SuspiciousOperation)):
            return None
        logging.getLogger("django").error("Request failed")
        return error500(request)
