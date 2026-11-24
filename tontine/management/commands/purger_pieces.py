"""Politique de conservation des pièces d'identité (§8, loi n° 2013-450).

Supprime les scans et photos :
- des dossiers refusés depuis plus de 3 mois ;
- des membres clôturés depuis plus de N mois (Paramètres).
Les métadonnées (numéro de pièce, dates) restent pour la traçabilité financière.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from tontine.models import Inscription, JournalAudit, Membre, Parametres


class Command(BaseCommand):
    help = "Purge les pièces d'identité arrivées en fin de durée de conservation."

    def add_arguments(self, parser):
        parser.add_argument("--simulation", action="store_true")

    def handle(self, *args, **o):
        p = Parametres.get()
        now = timezone.now()
        cibles = list(Inscription.objects.filter(statut=Inscription.Statut.REFUSEE,
                                                 decide_le__lt=now - timedelta(days=90),
                                                 pieces_purgees_le__isnull=True))
        cibles += [m.inscription for m in Membre.objects.filter(
            statut=Membre.Statut.CLOTURE, cloture_le__lt=now - timedelta(days=30 * p.conservation_pieces_mois),
            inscription__pieces_purgees_le__isnull=True).select_related("inscription")]
        for ins in cibles:
            if o["simulation"]:
                self.stdout.write(f"[simulation] {ins.reference} — {ins.nom_complet}")
                continue
            ins.purger_pieces()
            JournalAudit.objects.create(action="piece.purgee", objet=ins.reference,
                                        details="Fin de durée de conservation")
        self.stdout.write(self.style.SUCCESS(f"{len(cibles)} dossier(s) traité(s)."))
