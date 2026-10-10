from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.access.services import guarded_transaction
from apps.audit.models import AuditEntry
from apps.audit.services import record_action


class Command(BaseCommand):
    help = "Development-маркер для проверки сохранности данных, без тестовых сотрудников."

    def add_arguments(self, parser):
        parser.add_argument("--marker", required=True)
        parser.add_argument("--check-only", action="store_true")

    def handle(self, *args, **options):
        if settings.SETTINGS_MODULE != "config.settings.development":
            raise CommandError("Маркер доступен только в development.")
        marker = options["marker"]
        if len(marker) > 100:
            raise CommandError("Маркер должен быть не длиннее 100 символов.")
        with guarded_transaction(write=not options["check_only"]):
            entry = AuditEntry.objects.filter(action="environment.probe_created", object_id=marker).first()
            if entry is None:
                if options["check_only"]:
                    raise CommandError("Маркер отсутствует.")
                record_action(actor=None, action="environment.probe_created", object_type="environment", object_id=marker, changes={"marker": marker})
        self.stdout.write("OK: development-маркер сохранен и доступен в БД.")
