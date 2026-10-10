from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Явно выполнить миграции и применить runtime-права. Web этого не делает."

    def handle(self, *args, **options):
        call_command("migrate", interactive=False)
        call_command("grant_runtime_access")
