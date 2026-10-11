from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction


class Command(BaseCommand):
    help = "Выдать минимальный runtime DML после миграций (запуск владельцем схемы)."

    def handle(self, *args, **options):
        import os

        role = os.environ.get("DB_APP_USER", "")
        if not role:
            raise CommandError("Нужен DB_APP_USER.")
        quoted = connection.ops.quote_name(role)
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT current_user")
            if cursor.fetchone()[0] == role:
                raise CommandError("Эту команду запускает миграционная учетная запись.")
            cursor.execute(f"GRANT USAGE ON SCHEMA public TO {quoted}")
            cursor.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {quoted}")
            cursor.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {quoted}")
            cursor.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_auditentry FROM {quoted}")
            cursor.execute(f"REVOKE INSERT, DELETE, TRUNCATE ON access_accessguard FROM {quoted}")
            cursor.execute(f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON access_permission FROM {quoted}")
            for table in ["audienceselection", "audienceselectionuser", "audienceselectionrole", "audienceselectiongroup", "assignmentsource", "mutationreceipt"]:
                cursor.execute("SELECT to_regclass(%s)", [f"workitems_{table}"])
                if cursor.fetchone()[0] is not None:
                    cursor.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON workitems_{table} FROM {quoted}")
            cursor.execute("SELECT to_regclass('discussions_eventmessage')")
            if cursor.fetchone()[0] is not None:
                cursor.execute(f"REVOKE DELETE, TRUNCATE ON discussions_eventmessage FROM {quoted}")
            # Runtime не меняет структуру, права, аудит или историю миграций.
            cursor.execute(f"REVOKE CREATE ON SCHEMA public FROM {quoted}")
            cursor.execute(f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON django_migrations FROM {quoted}")
        self.stdout.write("Минимальные runtime-права применены.")
