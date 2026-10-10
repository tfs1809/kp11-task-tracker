from django.core.management import call_command
from django.test.runner import DiscoverRunner


class PostgresTestRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        configuration = super().setup_databases(**kwargs)
        call_command("grant_runtime_access", verbosity=0)
        return configuration
