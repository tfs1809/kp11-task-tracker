from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.limits import RateLimited
from apps.accounts.services import bootstrap_administrator


class Command(BaseCommand):
    help = "Однократно пригласить первого администратора; пароль задается только через почту."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--full-name", required=True)
        parser.add_argument("--resend", action="store_true")

    def handle(self, *args, **options):
        try:
            result = bootstrap_administrator(email=options["email"], full_name=options["full_name"], resend=options["resend"])
        except ValidationError as error:
            raise CommandError(" ".join(error.messages)) from None
        except RateLimited:
            raise CommandError("Лимит приглашений исчерпан. Повторите позже.") from None
        if not result.sent:
            raise CommandError("Приглашение сохранено, SMTP-доставка не подтверждена. Исправьте SMTP и используйте --resend.")
        self.stdout.write("Первое приглашение доставлено SMTP; ссылка находится только в письме.")
