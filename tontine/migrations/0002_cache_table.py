from django.core.management import call_command
from django.db import migrations


def creer_table_cache(apps, schema_editor):
    call_command("createcachetable", verbosity=0)


class Migration(migrations.Migration):
    dependencies = [("tontine", "0001_initial")]
    operations = [migrations.RunPython(creer_table_cache, migrations.RunPython.noop)]
