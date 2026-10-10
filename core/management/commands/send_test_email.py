from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError
import re


class Command(BaseCommand):
    help = "Отправить несекретное development-письмо в Mailpit."

    def add_arguments(self, parser):
        parser.add_argument("--marker", default="")

    def handle(self, *args, **options):
        if settings.SETTINGS_MODULE != "config.settings.development" or settings.EMAIL_HOST != "mailpit":
            raise CommandError("Тестовая отправка доступна только development с Mailpit.")
        marker = options["marker"]
        if marker and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", marker):
            raise CommandError("Недопустимый тестовый маркер.")
        subject = "KP11: проверка среды" + (f" [{marker}]" if marker else "")
        count = send_mail(subject, "Django доставил тестовое письмо через SMTP.", settings.DEFAULT_FROM_EMAIL, ["environment-check@corp.kp11.ru"], fail_silently=False)
        if count != 1:
            raise CommandError("SMTP не подтвердил отправку.")
        self.stdout.write("SMTP подтвердил отправку одного development-письма.")
