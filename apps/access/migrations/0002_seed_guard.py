from django.db import migrations


def seed_guard(apps, schema_editor):
    apps.get_model("access", "AccessGuard").objects.get_or_create(pk=1)


class Migration(migrations.Migration):
    dependencies = [("access", "0001_initial")]
    operations = [migrations.RunPython(seed_guard, migrations.RunPython.noop)]
