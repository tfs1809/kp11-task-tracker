from functools import wraps

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from apps.access.administration import VersionConflict, save_role
from apps.accounts.administration import edit_employee
from apps.audiences.services import save_group
from apps.access.models import Role
from apps.access.policies import require_permission
from apps.access.services import actor_transaction
from apps.audiences.models import AudienceGroup
from core.middleware import public_view

from .forms import ChangePasswordForm, EmployeeForm, GroupForm, InvitationForm, LoginForm, NewPasswordForm, RecoveryForm, RoleForm
from .limits import RateLimited, login_limits, recovery_limits
from .models import User
from .selectors import administration_overview
from .services import activate_employee, change_password, invite_employee, login_employee, logout_employee, request_recovery, resend_invitation, reset_password, valid_invitation, valid_reset


def limited(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except RateLimited as error:
            response = render(request, "errors/error.html", {"code": 429, "message": "Слишком много запросов. Повторите позже."}, status=429)
            response["Retry-After"] = error.retry_after
            return response
    return wrapper


def form_page(request, form, title, *, status=200, message="", submit="Сохранить", **context):
    values = {"form": form, "title": title, "message": message, "submit": submit, **context}
    if getattr(request.resolver_match.func, "public_access", False):
        return render(request, "accounts/form.html", values, status=status)
    # Ошибочная POST-форма тоже читает справочники/профиль. Проверять права
    # повторно и держать read guard до окончания рендера, после write service.
    with actor_transaction(request.user.pk, session_auth_version=request.session.get("auth_version")) as actor:
        if getattr(request, "required_permission", None):
            require_permission(actor, request.required_permission)
        return render(request, "accounts/form.html", values, status=status)


def form_errors(form, operation):
    try:
        return operation(), 200
    except VersionConflict as error:
        form.add_error(None, " ".join(error.messages))
        return None, 409
    except ValidationError as error:
        # Model validation dictionaries contain field names not present in every form.
        form.add_error(None, " ".join(error.messages))
        return None, 400
    except IntegrityError:
        form.add_error(None, "Запись с таким адресом или названием уже существует.")
        return None, 409


@public_view
@limited
@require_http_methods(["GET", "POST"])
def login_view(request):
    form = LoginForm(request.POST or None)
    status = 200
    if request.method == "POST":
        login_limits(request.POST.get("email", "")[:254], request.META.get("REMOTE_ADDR", ""))
        if form.is_valid():
            _, status = form_errors(form, lambda: login_employee(request, **form.cleaned_data))
            if status == 200:
                target = request.POST.get("next", "/")
                if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                    target = "/"
                return redirect(target)
    return form_page(request, form, "Вход", status=status, submit="Войти", next=request.GET.get("next", "/"), login_page=True)


@require_POST
def logout_view(request):
    logout_employee(request)
    return redirect("login")


@public_view
@limited
@require_http_methods(["GET", "POST"])
def recovery_view(request):
    form = RecoveryForm(request.POST or None)
    if request.method == "POST":
        recovery_limits(request.POST.get("email", "")[:254], request.META.get("REMOTE_ADDR", ""))
        if form.is_valid():
            request_recovery(form.cleaned_data["email"])
            # Один и тот же текст/HTTP статус для любого адреса и SMTP ошибки.
            return form_page(request, None, "Восстановление доступа", message="Если адрес принадлежит действующему активированному сотруднику, на него отправлено письмо.")
    return form_page(request, form, "Восстановление доступа", submit="Отправить письмо")


def password_link_page(request, *, activate):
    token = request.POST.get("token", "") if request.method == "POST" else request.GET.get("token", "")
    validator = valid_invitation if activate else valid_reset
    try:
        validator(token)
    except ValidationError:
        return form_page(request, None, "Ссылка недействительна", message="Ссылка недействительна или срок ее действия истек. Запросите новую.", status=400)
    form = NewPasswordForm(request.POST or None, initial={"token": token})
    status = 200
    if request.method == "POST" and form.is_valid():
        operation = activate_employee if activate else reset_password
        _, status = form_errors(form, lambda: operation(token=token, password=form.cleaned_data["password"]))
        if status == 200:
            return form_page(request, None, "Пароль сохранен", message="Пароль сохранен. Теперь войдите с корпоративной почтой.", finished=True)
    return form_page(request, form, "Активация доступа" if activate else "Новый пароль", status=status, password_help=True)


@public_view
@require_http_methods(["GET", "POST"])
def activation_view(request):
    return password_link_page(request, activate=True)


@public_view
@require_http_methods(["GET", "POST"])
def reset_view(request):
    return password_link_page(request, activate=False)


@require_http_methods(["GET", "POST"])
def password_change_view(request):
    form = ChangePasswordForm(request.POST or None)
    status = 200
    if request.method == "POST" and form.is_valid():
        _, status = form_errors(form, lambda: change_password(request, old_password=form.cleaned_data["old_password"], password=form.cleaned_data["password"]))
        if status == 200:
            return form_page(request, None, "Пароль изменен", message="Пароль изменен. Остальные сессии отозваны.")
    return form_page(request, form, "Изменение пароля", status=status, password_help=True)


@require_http_methods(["GET"])
def administration_view(request):
    return render(request, "accounts/administration.html", administration_overview(request.user))


def require_admin_request(request, code):
    # POST не держит guard middleware; ранняя проверка не заменяет service-проверку.
    with actor_transaction(request.user.pk, session_auth_version=request.session.get("auth_version")) as actor:
        require_permission(actor, code)
    request.required_permission = code


@limited
@require_http_methods(["GET", "POST"])
def invitation_view(request):
    require_admin_request(request, "accounts.manage_users")
    form = InvitationForm(request.POST or None)
    status = 200
    if request.method == "POST" and form.is_valid():
        result, status = form_errors(form, lambda: invite_employee(actor_id=request.user.pk, auth_version=request.session.get("auth_version"), email=form.cleaned_data["email"], full_name=form.cleaned_data["full_name"], role_ids=[role.pk for role in form.cleaned_data["roles"]], group_ids=[group.pk for group in form.cleaned_data["groups"]]))
        if status == 200:
            user, delivery = result
            return form_page(request, None, "Сотрудник приглашен", message="Письмо передано SMTP." if delivery.sent else "Приглашение сохранено, доставка письма не подтверждена. Исправьте SMTP и повторите отправку со страницы сотрудника.", employee_id=user.pk)
    return form_page(request, form, "Пригласить сотрудника", status=status, submit="Пригласить")


@require_http_methods(["GET", "POST"])
def employee_view(request, user_id):
    require_admin_request(request, "accounts.manage_users")
    user = get_object_or_404(User, pk=user_id)
    initial = {"full_name": user.full_name, "is_active": user.is_active, "version": user.version, "roles": user.user_roles.values_list("role_id", flat=True), "groups": user.memberships.values_list("group_id", flat=True)}
    form = EmployeeForm(request.POST or None, initial=initial)
    status = 200
    if request.method == "POST" and form.is_valid():
        _, status = form_errors(form, lambda: edit_employee(actor_id=request.user.pk, auth_version=request.session.get("auth_version"), user_id=user.pk, expected_version=form.cleaned_data["version"], full_name=form.cleaned_data["full_name"], is_active=form.cleaned_data["is_active"], role_ids=[role.pk for role in form.cleaned_data["roles"]], group_ids=[group.pk for group in form.cleaned_data["groups"]]))
        if status == 200:
            return redirect("administration")
    return form_page(request, form, "Сотрудник: " + user.email, status=status, pending_employee=user if not user.email_verified_at and user.is_active else None)


@limited
@require_POST
def resend_view(request, user_id):
    require_admin_request(request, "accounts.manage_users")
    get_object_or_404(User, pk=user_id)
    try:
        delivery = resend_invitation(actor_id=request.user.pk, auth_version=request.session.get("auth_version"), user_id=user_id)
    except ValidationError as error:
        return form_page(request, None, "Приглашение не отправлено", status=400, message=" ".join(error.messages))
    return form_page(request, None, "Приглашение обновлено", message="Старая ссылка отозвана. Новое письмо передано SMTP." if delivery.sent else "Старая ссылка отозвана. SMTP-доставка не подтверждена; исправьте почту и повторите отправку.")


@require_http_methods(["GET", "POST"])
def role_view(request, role_id=None):
    require_admin_request(request, "access.manage_roles")
    role = get_object_or_404(Role, pk=role_id) if role_id else None
    initial = {"name": role.name, "is_active": role.is_active, "version": role.version, "permissions": role.permissions.all()} if role else {}
    form = RoleForm(request.POST or None, initial=initial)
    status = 200
    if request.method == "POST" and form.is_valid():
        _, status = form_errors(form, lambda: save_role(actor_id=request.user.pk, auth_version=request.session.get("auth_version"), role_id=role_id, expected_version=form.cleaned_data["version"], name=form.cleaned_data["name"], is_active=form.cleaned_data["is_active"], permission_codes=[permission.pk for permission in form.cleaned_data["permissions"]]))
        if status == 200:
            return redirect("administration")
    return form_page(request, form, "Изменить роль" if role else "Создать роль", status=status, message="Системная роль администратора сохраняет полный каталог прав." if role and role.is_system_admin else "Административные разрешения доступны только системной роли администратора.")


@require_http_methods(["GET", "POST"])
def group_view(request, group_id=None):
    require_admin_request(request, "audiences.manage_groups")
    group = get_object_or_404(AudienceGroup, pk=group_id) if group_id else None
    initial = {"name": group.name, "is_active": group.is_active, "version": group.version} if group else {}
    form = GroupForm(request.POST or None, initial=initial)
    status = 200
    if request.method == "POST" and form.is_valid():
        _, status = form_errors(form, lambda: save_group(actor_id=request.user.pk, auth_version=request.session.get("auth_version"), group_id=group_id, expected_version=form.cleaned_data["version"], name=form.cleaned_data["name"], is_active=form.cleaned_data["is_active"]))
        if status == 200:
            return redirect("administration")
    return form_page(request, form, "Изменить группу адресатов" if group else "Создать группу адресатов", status=status)
