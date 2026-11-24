"""Initialise la plateforme : formules officielles, paramètres et compte administratrice.

Usage : python manage.py init_fatontine --email fatima@exemple.ci
Le mot de passe est demandé de façon interactive (ou via ADMIN_PASSWORD).
"""
import getpass
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError

from tontine.models import Formule, Parametres

FORMULES = [(550, 50_000), (1_100, 100_000), (2_200, 200_000), (4_400, 400_000), (5_500, 500_000),
            (11_000, 1_000_000), (22_000, 2_000_000), (33_000, 3_000_000)]


class Command(BaseCommand):
    help = "Crée les 8 formules officielles, les paramètres et le compte de l'administratrice."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="fatima")
        parser.add_argument("--email", default=os.environ.get("ADMIN_EMAIL", ""))
        parser.add_argument("--first-name", default="Fatima")
        parser.add_argument("--sans-admin", action="store_true", help="Ne crée que les formules.")

    def handle(self, *args, **o):
        Parametres.get()
        for i, (cot, rec) in enumerate(FORMULES, 1):
            _, cree = Formule.objects.get_or_create(code=f"F{i}", defaults={
                "cotisation_journaliere": cot, "reception_annoncee": rec, "ordre": i})
            if cree:
                self.stdout.write(f"  Formule F{i} : {cot} F/jour → {rec} F")
        self.stdout.write(self.style.SUCCESS("Formules prêtes."))
        if o["sans_admin"]:
            return

        User = get_user_model()
        if User.objects.filter(username=o["username"]).exists():
            self.stdout.write(f"Le compte « {o['username']} » existe déjà.")
            return
        if not o["email"]:
            raise CommandError("Indiquez l'e-mail (--email) : il sert à réinitialiser le mot de passe.")
        mdp = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Mot de passe de l'administratrice : ")
        user = User(username=o["username"], email=o["email"], first_name=o["first_name"])
        try:
            validate_password(mdp, user)
        except Exception as e:
            raise CommandError(" ".join(e.messages))
        user.set_password(mdp)
        user.is_staff = True
        user.is_superuser = True
        user.save()
        self.stdout.write(self.style.SUCCESS(f"Compte administratrice « {user.username} » créé."))
