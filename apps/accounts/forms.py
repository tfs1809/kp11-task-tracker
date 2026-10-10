from django import forms

from apps.access.models import Permission, Role
from apps.audiences.models import AudienceGroup

from .models import normalize_corporate_email


class LoginForm(forms.Form):
    email = forms.CharField(label="Корпоративная почта", max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "username"}))
    password = forms.CharField(label="Пароль", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


class RecoveryForm(forms.Form):
    email = forms.CharField(label="Корпоративная почта", max_length=254, widget=forms.EmailInput(attrs={"autocomplete": "email"}))


class NewPasswordForm(forms.Form):
    token = forms.CharField(widget=forms.HiddenInput)
    password = forms.CharField(label="Новый пароль", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password_confirm = forms.CharField(label="Повторите пароль", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def clean(self):
        data = super().clean()
        if data.get("password") != data.get("password_confirm"):
            self.add_error("password_confirm", "Пароли не совпадают.")
        return data


class ChangePasswordForm(NewPasswordForm):
    token = None
    old_password = forms.CharField(label="Текущий пароль", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


class EmployeeForm(forms.Form):
    full_name = forms.CharField(label="ФИО", max_length=255)
    is_active = forms.BooleanField(label="Действует", required=False, initial=True)
    roles = forms.ModelMultipleChoiceField(label="Роли (дают права)", queryset=Role.objects.all(), required=False, widget=forms.CheckboxSelectMultiple)
    groups = forms.ModelMultipleChoiceField(label="Группы адресатов (не дают прав)", queryset=AudienceGroup.objects.all(), required=False, widget=forms.CheckboxSelectMultiple)
    version = forms.IntegerField(widget=forms.HiddenInput, min_value=1)


class InvitationForm(EmployeeForm):
    email = forms.CharField(label="Корпоративная почта", max_length=254, widget=forms.EmailInput)
    is_active = None
    version = None

    def clean_email(self):
        return normalize_corporate_email(self.cleaned_data["email"])


class RoleForm(forms.Form):
    name = forms.CharField(label="Название роли", max_length=255)
    is_active = forms.BooleanField(label="Действует", required=False, initial=True)
    permissions = forms.ModelMultipleChoiceField(label="Разрешения", queryset=Permission.objects.all().order_by("code"), required=False, widget=forms.CheckboxSelectMultiple)
    version = forms.IntegerField(widget=forms.HiddenInput, min_value=1, required=False)


class GroupForm(forms.Form):
    name = forms.CharField(label="Название группы адресатов", max_length=255)
    is_active = forms.BooleanField(label="Действует", required=False, initial=True)
    version = forms.IntegerField(widget=forms.HiddenInput, min_value=1, required=False)
