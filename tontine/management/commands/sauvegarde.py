"""Sauvegarde de la base (§8) : export JSON compressé horodaté + rotation.

À planifier chaque nuit (cron) : python manage.py sauvegarde --garder 30
Pour PostgreSQL, compléter par pg_dump (voir docs/DEPLOIEMENT.md).
"""
import gzip
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Sauvegarde les données dans BACKUP_DIR (JSON compressé)."

    def add_arguments(self, parser):
        parser.add_argument("--garder", type=int, default=30, help="Nombre de sauvegardes à conserver.")

    def handle(self, *args, **o):
        dossier = settings.BACKUP_DIR
        dossier.mkdir(parents=True, exist_ok=True)
        out = StringIO()
        call_command("dumpdata", "tontine", "auth.user", "--natural-foreign", "--indent", "1", stdout=out)
        fichier = dossier / f"fatontine-{timezone.localtime():%Y%m%d-%H%M%S}.json.gz"
        with gzip.open(fichier, "wt", encoding="utf-8") as f:
            f.write(out.getvalue())
        fichier.chmod(0o600)
        anciens = sorted(dossier.glob("fatontine-*.json.gz"))[:-o["garder"]]
        for a in anciens:
            a.unlink()
        self.stdout.write(self.style.SUCCESS(f"Sauvegarde : {fichier} ({len(anciens)} ancienne(s) supprimée(s))"))
        self.stdout.write("Restauration : python manage.py loaddata <fichier.json.gz>")
