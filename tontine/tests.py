from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Cotisation, Formule, Groupe, Inscription, JournalAudit, Membre, Tour

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


def fichier(nom="cni.png"):
    return SimpleUploadedFile(nom, PNG, content_type="image/png")


@override_settings(PRIVATE_MEDIA_ROOT="/tmp/fatontine-test-media", MEDIA_ROOT="/tmp/fatontine-test-media")
class ParcoursComplet(TestCase):
    def setUp(self):
        cache.clear()
        call_command("init_fatontine", "--sans-admin", stdout=StringIO())
        self.admin = User.objects.create_user("fatima", "fatima@exemple.ci", "UnMotDePasse-Solide-26", first_name="Fatima")
        self.client.login(username="fatima", password="UnMotDePasse-Solide-26")
        self.f1 = Formule.objects.get(code="F1")
        self.groupe = Groupe.objects.create(nom="Groupe A", formule=self.f1,
                                            date_demarrage=timezone.localdate() - timedelta(days=4))

    def _inscription(self, nom="KOUASSI", tel="0700000001"):
        r = self.client.post(reverse("inscription_nouvelle"), {
            "nom": nom, "prenoms": "Aya", "sexe": "F", "date_naissance": "1990-01-01", "telephone": tel,
            "adresse": "Abidjan, Cocody", "type_piece": "cni", "numero_piece": "CI0001", "fichier_piece": fichier(),
            "photo": fichier("photo.png"), "temoin_nom": "KONÉ Ibrahim", "temoin_telephone": "0500000000",
            "formule_souhaitee": self.f1.pk, "consentement": "on"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", {}) and r.context["form"].errors)
        return Inscription.objects.get(nom=nom)

    def _valider(self, ins):
        return self.client.post(reverse("inscription_valider", args=[ins.pk]),
                                {"groupe": self.groupe.pk, "pieces_verifiees": "on"})

    def test_formules_officielles_reception_10_sur_11(self):
        from decimal import Decimal
        self.assertEqual(Formule.objects.count(), 8)
        for f in Formule.objects.all():
            self.assertEqual(f.ratio_beneficiaire, Decimal(10) / Decimal(11))
            self.assertEqual(f.frais_gestion * 10, f.reception_annoncee)  # frais = 10 % de la réception

    def test_site_prive(self):
        self.client.logout()
        r = self.client.get(reverse("dashboard"))
        self.assertRedirects(r, "/connexion/?next=/", fetch_redirect_response=False)

    def test_inscription_puis_validation_cree_membre_et_tour(self):
        ins = self._inscription()
        self.assertEqual(ins.statut, Inscription.Statut.EN_ATTENTE)
        self.assertFalse(Membre.objects.exists())
        self._valider(ins)
        m = Membre.objects.get()
        self.assertEqual(m.groupe, self.groupe)
        t = Tour.objects.get()
        self.assertEqual((t.rang, t.montant), (1, 50_000))
        self.assertEqual(t.date_prevue, self.groupe.date_demarrage + timedelta(days=9))

    def test_groupe_limite_a_10(self):
        for i in range(11):
            ins = self._inscription(nom=f"MEMBRE{i}", tel=f"07000000{i:02d}")
            self._valider(ins)
        self.assertEqual(Membre.objects.count(), 10)
        self.assertEqual(Inscription.objects.filter(statut="en_attente").count(), 1)

    def test_pieces_privees_reservees_admin(self):
        ins = self._inscription()
        url = reverse("piece_privee", args=[ins.pk, "fichier_piece"])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_fichier_non_image_refuse(self):
        r = self.client.post(reverse("inscription_nouvelle"), {
            "nom": "X", "prenoms": "Y", "sexe": "F", "date_naissance": "1990-01-01", "telephone": "0700000009",
            "adresse": "A", "type_piece": "cni", "numero_piece": "1",
            "fichier_piece": SimpleUploadedFile("cni.png", b"<script>", content_type="image/png"),
            "photo": fichier(), "temoin_nom": "T", "temoin_telephone": "0500000001",
            "formule_souhaitee": self.f1.pk, "consentement": "on"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("fichier_piece", r.context["form"].errors)

    def test_cotisation_wave_retard_et_unicite(self):
        self._valider(self._inscription())
        m = Membre.objects.get()
        self.assertEqual(m.ecart(), 5 * 550)  # 5 jours écoulés, rien payé
        data = {"membre": m.pk, "nb_jours": 3, "date_paiement": timezone.localdate().isoformat(),
                "mode": "wave", "id_wave": "t_ci123", "verifie": "on"}
        r = self.client.post(reverse("cotisations"), data)
        c = Cotisation.objects.get()
        self.assertRedirects(r, reverse("recu", args=[c.pk]))
        self.assertEqual((c.montant, c.id_wave), (1650, "T_CI123"))
        self.assertTrue(c.numero_recu.startswith("REC-"))
        self.assertEqual(m.ecart(), 2 * 550)
        # Même ID Wave refusé
        r = self.client.post(reverse("cotisations"), data)
        self.assertEqual(Cotisation.objects.count(), 1)
        # Paiement non vérifié refusé
        data2 = dict(data, id_wave="", mode="especes")
        data2.pop("verifie")
        self.client.post(reverse("cotisations"), data2)
        self.assertEqual(Cotisation.objects.count(), 1)
        # PDF
        r = self.client.get(reverse("recu_pdf", args=[c.pk]))
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_annulation_trace_sans_suppression(self):
        self._valider(self._inscription())
        m = Membre.objects.get()
        self.client.post(reverse("cotisations"), {"membre": m.pk, "nb_jours": 1, "mode": "especes", "verifie": "on",
                                                   "date_paiement": timezone.localdate().isoformat()})
        c = Cotisation.objects.get()
        self.client.post(reverse("cotisation_annuler", args=[c.pk]), {"motif": "Erreur de saisie"})
        c.refresh_from_db()
        self.assertTrue(c.annulee)
        self.assertEqual(m.total_verse(), 0)
        self.assertTrue(JournalAudit.objects.filter(action="cotisation.annulee").exists())

    def test_echange_de_rang_justifie(self):
        self._valider(self._inscription("A", "0700000001"))
        self._valider(self._inscription("B", "0700000002"))
        t1 = Tour.objects.get(rang=1)
        r = self.client.post(reverse("tour_detail", args=[t1.pk]),
                             {"action": "rang", "nouveau_rang": 2, "justification": "Accord mutuel"})
        self.assertEqual(r.status_code, 302)
        t1.refresh_from_db()
        self.assertEqual(t1.rang, 2)
        self.assertEqual(Tour.objects.get(rang=1).membre.nom, "B")
        self.assertEqual(t1.date_prevue, self.groupe.date_tour(2))

    def test_distribution(self):
        self._valider(self._inscription())
        t = Tour.objects.get()
        self.client.post(reverse("tour_detail", args=[t.pk]), {"action": "distribuer", "mode": "especes"})
        t.refresh_from_db()
        self.assertEqual(t.statut, Tour.Statut.DISTRIBUE)

    def test_pages_et_exports(self):
        self._valider(self._inscription())
        m = Membre.objects.get()
        for nom in ("dashboard", "inscriptions", "membres", "groupes", "cotisations", "beneficiaires",
                    "transactions", "notifications", "rapports", "parametres", "journal", "formules"):
            self.assertEqual(self.client.get(reverse(nom)).status_code, 200, nom)
        for rapport, q in (("periode", "?periode=mois"), ("groupe", f"?groupe={self.groupe.pk}"),
                           ("membre", f"?membre={m.pk}"), ("retards", ""), ("distributions", ""),
                           ("membres", ""), ("cotisations", "")):
            for fmt in ("pdf", "xlsx"):
                r = self.client.get(reverse("export", args=[rapport, fmt]) + q)
                self.assertEqual(r.status_code, 200, f"{rapport}.{fmt}")


class Connexion(TestCase):
    def setUp(self):
        cache.clear()
        User.objects.create_user("fatima", "f@e.ci", "UnMotDePasse-Solide-26")

    def test_blocage_apres_5_echecs(self):
        for _ in range(5):
            self.client.post(reverse("login"), {"username": "fatima", "password": "faux"})
        r = self.client.post(reverse("login"), {"username": "fatima", "password": "UnMotDePasse-Solide-26"})
        self.assertEqual(r.status_code, 429)

    def test_connexion_reussie(self):
        r = self.client.post(reverse("login"), {"username": "fatima", "password": "UnMotDePasse-Solide-26"})
        self.assertRedirects(r, reverse("dashboard"), fetch_redirect_response=False)
