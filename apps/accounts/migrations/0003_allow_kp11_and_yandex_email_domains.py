from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_ratelimitbucket_invitation_passwordresetgrant"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="user",
            name="user_email_corporate",
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=(models.Q(email__endswith="@kp11.ru") | models.Q(email__endswith="@yandex.ru")),
                name="user_email_corporate",
            ),
        ),
    ]
