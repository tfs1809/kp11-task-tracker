from django.db import migrations

PERMISSIONS = {'workitems.create': 'Создание задач и событий', 'workitems.view_own': 'Просмотр выданных заданий', 'workitems.edit_own': 'Изменение выданных заданий', 'workitems.manage_own_assignees': 'Управление своими исполнителями', 'workitems.view_all': 'Просмотр всех заданий', 'workitems.edit_all': 'Изменение всех заданий', 'accounts.manage_users': 'Управление сотрудниками', 'access.manage_roles': 'Управление ролями', 'audiences.manage_groups': 'Управление группами адресатов', 'discussions.moderate': 'Модерация чата доступного события', 'discussions.send_accessible': 'Отправка сообщений в доступное событие', 'audit.view': 'Просмотр истории'}
ADMIN_ONLY = {'accounts.manage_users', 'audiences.manage_groups', 'access.manage_roles'}
MANAGER = {'workitems.create', 'workitems.edit_own', 'workitems.manage_own_assignees', 'workitems.view_own'}

def seed(apps, schema_editor):
    Permission = apps.get_model("access", "Permission")
    Role = apps.get_model("access", "Role")
    RolePermission = apps.get_model("access", "RolePermission")
    Group = apps.get_model("audiences", "AudienceGroup")
    for code, name in PERMISSIONS.items():
        Permission.objects.get_or_create(code=code, defaults={"name": name, "admin_only": code in ADMIN_ONLY})
    for key, name in [("administrator", "Администратор"), ("manager", "Руководитель"), ("curator", "Куратор"), ("teacher", "Преподаватель")]:
        role, _ = Role.objects.get_or_create(key=key, defaults={"name": name, "is_system_admin": key == "administrator"})
        for code in PERMISSIONS if key == "administrator" else MANAGER if key == "manager" else []:
            RolePermission.objects.get_or_create(role=role, permission_id=code)
    for name in ["Кураторы 1-го курса", "Кураторы 2-го курса", "Кураторы выпускного курса"]:
        Group.objects.get_or_create(name=name)


SQL = """
CREATE FUNCTION protect_role_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.is_system_admin THEN RAISE EXCEPTION 'protected system role' USING ERRCODE='23514'; END IF;
        RETURN OLD;
    END IF;
    IF NEW.key IS DISTINCT FROM OLD.key OR NEW.is_system_admin IS DISTINCT FROM OLD.is_system_admin THEN
        RAISE EXCEPTION 'immutable role identity' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER role_identity BEFORE UPDATE OR DELETE ON access_role FOR EACH ROW EXECUTE FUNCTION protect_role_identity();
CREATE FUNCTION protect_role_permission() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE system_role boolean; restricted boolean;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT is_system_admin INTO system_role FROM access_role WHERE id=OLD.role_id;
        IF system_role THEN RAISE EXCEPTION 'protected administrator catalog' USING ERRCODE='23514'; END IF;
    END IF;
    IF TG_OP != 'DELETE' THEN
        SELECT is_system_admin INTO system_role FROM access_role WHERE id=NEW.role_id;
        SELECT admin_only INTO restricted FROM access_permission WHERE code=NEW.permission_id;
        IF restricted AND NOT system_role THEN RAISE EXCEPTION 'administrator-only permission' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    RETURN OLD;
END $$;
CREATE TRIGGER role_permission_policy BEFORE INSERT OR UPDATE OR DELETE ON access_rolepermission FOR EACH ROW EXECUTE FUNCTION protect_role_permission();
CREATE FUNCTION protect_employee_email() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.email IS DISTINCT FROM OLD.email THEN RAISE EXCEPTION 'immutable employee email' USING ERRCODE='23514'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER employee_email BEFORE UPDATE ON accounts_user FOR EACH ROW EXECUTE FUNCTION protect_employee_email();
"""
REVERSE_SQL = """
DROP TRIGGER employee_email ON accounts_user;
DROP FUNCTION protect_employee_email();
DROP TRIGGER role_permission_policy ON access_rolepermission;
DROP FUNCTION protect_role_permission();
DROP TRIGGER role_identity ON access_role;
DROP FUNCTION protect_role_identity();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("access", "0003_permission_role_rolepermission_role_permissions_and_more"),
        ("audiences", "0001_initial"),
    ]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop), migrations.RunSQL(SQL, REVERSE_SQL)]
