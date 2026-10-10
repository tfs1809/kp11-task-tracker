from django.contrib.auth.backends import BaseBackend

from .models import User


class ClosedBackend(BaseBackend):
    """Вход не реализован до этапа 2; session lookup ограничен действующим сотрудником."""

    def authenticate(self, request, **credentials):
        return None

    def get_user(self, user_id):
        return User.objects.filter(pk=user_id, is_active=True, email_verified_at__isnull=False).first()
