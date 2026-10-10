from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ValidationError

from .models import User, normalize_corporate_email


class EmailBackend(BaseBackend):
    def authenticate(self, request, email=None, password=None, **credentials):
        try:
            normalized = normalize_corporate_email(email or "")
        except ValidationError:
            normalized = ""
        user = User.objects.filter(email=normalized).first()
        if user is None:
            User().set_password(password or "")
            return None
        if check_password(password or "", user.password) and user.is_active and user.email_verified_at and user.has_usable_password():
            return user
        return None

    def get_user(self, user_id):
        return User.objects.filter(pk=user_id, is_active=True, email_verified_at__isnull=False).first()
