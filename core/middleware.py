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
        with guarded_transaction(write=request.method not in {"GET", "HEAD", "OPTIONS"}):
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
            return view_func(request, *view_args, **view_kwargs)

    def process_response(self, request, response):
        response["Cache-Control"] = "no-store"
        return response
