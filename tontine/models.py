"""Modèle de données FATONTINE CASH.

Principes (voir docs/SYNTHESE.md) :
- aucune opération financière n'est supprimée : elle est annulée avec un motif ;
- chaque opération possède une référence unique ;
- un ID Wave ne peut être utilisé qu'une seule fois ;
- un membre n'existe qu'après validation manuelle de son dossier.
"""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone


def private_upload(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"pieces/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.{ext}"


# ---------------------------------------------------------------------------
# Paramètres
# ---------------------------------------------------------------------------
class Parametres(models.Model):
    """Paramètres globaux (une seule ligne)."""

    taille_groupe = models.PositiveSmallIntegerField("Participants par groupe", default=10,
                                                     validators=[MinValueValidator(2), MaxValueValidator(50)])
    jours_par_tour = models.PositiveSmallIntegerField("Jours entre deux distributions", default=10,
                                                      validators=[MinValueValidator(1), MaxValueValidator(90)])
    conservation_pieces_mois = models.PositiveSmallIntegerField(
        "Conservation des pièces après clôture (mois)", default=24)
    telephone_contact = models.CharField("Téléphone de contact", max_length=30, blank=True, default="")
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Paramètres"
        verbose_name_plural = "Paramètres"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj



# ---------------------------------------------------------------------------
# Formules
# ---------------------------------------------------------------------------
class Formule(models.Model):
    code = models.CharField(max_length=10, unique=True)
    cotisation_journaliere = models.PositiveIntegerField("Cotisation journalière (FCFA)")
    reception_annoncee = models.PositiveIntegerField("Réception annoncée (FCFA)")
    active = models.BooleanField(default=True)
    ordre = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["ordre", "cotisation_journaliere"]

    def __str__(self):
        return f"{self.cotisation_journaliere:,} FCFA/jour → {self.reception_annoncee:,} FCFA".replace(",", " ")

    def collecte_theorique(self, taille=None, jours=None):
        p = Parametres.get()
        return self.cotisation_journaliere * (taille or p.taille_groupe) * (jours or p.jours_par_tour)

    @property
    def frais_gestion(self):
        """Différence entre la collecte théorique d'un tour et la réception annoncée."""
        return self.collecte_theorique() - self.reception_annoncee

    @property
    def ratio_beneficiaire(self) -> Decimal:
        theorique = self.collecte_theorique()
        return Decimal(self.reception_annoncee) / Decimal(theorique) if theorique else Decimal(0)

    @property
    def taux_gestion_pct(self) -> Decimal:
        return (Decimal(100) * (1 - self.ratio_beneficiaire)).quantize(Decimal("0.01"))


# ---------------------------------------------------------------------------
# Groupes
# ---------------------------------------------------------------------------
class Groupe(models.Model):
    class Statut(models.TextChoices):
        OUVERT = "ouvert", "Inscriptions ouvertes"
        EN_COURS = "en_cours", "Cycle en cours"
        TERMINE = "termine", "Cycle terminé"
        SUSPENDU = "suspendu", "Suspendu"

    nom = models.CharField(max_length=80, unique=True)
    formule = models.ForeignKey(Formule, on_delete=models.PROTECT, related_name="groupes")
    date_demarrage = models.DateField()
    capacite = models.PositiveSmallIntegerField(default=10)
    jours_par_tour = models.PositiveSmallIntegerField(default=10)
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.OUVERT)
    notes = models.TextField(blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date_demarrage", "nom"]

    def __str__(self):
        return self.nom

    @property
    def duree_jours(self):
        return self.capacite * self.jours_par_tour

    @property
    def date_fin(self):
        return self.date_demarrage + timedelta(days=self.duree_jours - 1)

    def membres_actifs(self):
        return self.membres.filter(statut=Membre.Statut.ACTIF)

    def places_occupees(self):
        return self.membres.exclude(statut=Membre.Statut.CLOTURE).count()

    def places_libres(self):
        return max(self.capacite - self.places_occupees(), 0)

    @property
    def est_complet(self):
        return self.places_libres() == 0

    def jours_ecoules(self, jour=None):
        jour = jour or timezone.localdate()
        if jour < self.date_demarrage:
            return 0
        return min((jour - self.date_demarrage).days + 1, self.duree_jours)

    def tour_courant(self, jour=None):
        j = self.jours_ecoules(jour)
        if j == 0:
            return 0
        return min((j - 1) // self.jours_par_tour + 1, self.capacite)

    def date_tour(self, rang: int):
        """Le bénéficiaire du rang n reçoit le dernier jour de sa période de 10 jours."""
        return self.date_demarrage + timedelta(days=rang * self.jours_par_tour - 1)

    def progression(self):
        return round(100 * self.jours_ecoules() / self.duree_jours) if self.duree_jours else 0

    def collecte(self):
        return Cotisation.valides().filter(groupe=self).aggregate(s=Sum("montant"))["s"] or 0

    def distribue(self):
        return self.tours.filter(statut=Tour.Statut.DISTRIBUE).aggregate(s=Sum("montant"))["s"] or 0

    def recalculer_dates_tours(self):
        for t in self.tours.all():
            nouvelle = self.date_tour(t.rang)
            if t.date_prevue != nouvelle and t.statut != Tour.Statut.DISTRIBUE:
                t.date_prevue = nouvelle
                t.save(update_fields=["date_prevue"])


# ---------------------------------------------------------------------------
# Inscriptions (dossiers d'adhésion)
# ---------------------------------------------------------------------------
class Inscription(models.Model):
    class Statut(models.TextChoices):
        EN_ATTENTE = "en_attente", "En attente"
        VALIDEE = "validee", "Validée"
        REFUSEE = "refusee", "Refusée"

    class Sexe(models.TextChoices):
        F = "F", "Féminin"
        M = "M", "Masculin"

    class TypePiece(models.TextChoices):
        CNI = "cni", "Carte nationale d'identité"
        EXTRAIT = "extrait", "Extrait d'acte de naissance"

    reference = models.CharField(max_length=20, unique=True, editable=False)
    nom = models.CharField(max_length=80)
    prenoms = models.CharField("Prénoms", max_length=120)
    sexe = models.CharField(max_length=1, choices=Sexe.choices)
    date_naissance = models.DateField("Date de naissance")
    telephone = models.CharField("Téléphone", max_length=30)
    adresse = models.CharField("Adresse / lieu de résidence", max_length=200)
    type_piece = models.CharField("Pièce justificative", max_length=10, choices=TypePiece.choices)
    numero_piece = models.CharField("Numéro de la pièce", max_length=40)
    fichier_piece = models.FileField("Scan de la pièce", upload_to=private_upload, blank=True)
    photo = models.FileField("Photo du membre", upload_to=private_upload, blank=True)
    temoin_nom = models.CharField("Nom et prénoms du témoin", max_length=150)
    temoin_telephone = models.CharField("Téléphone du témoin", max_length=30)
    formule_souhaitee = models.ForeignKey(Formule, on_delete=models.PROTECT, related_name="inscriptions")
    consentement = models.BooleanField("Consentement ARTCI (loi n° 2013-450)", default=False)
    consentement_le = models.DateTimeField(null=True, blank=True)
    statut = models.CharField(max_length=12, choices=Statut.choices, default=Statut.EN_ATTENTE)
    motif_decision = models.TextField("Motif / observation", blank=True)
    decide_le = models.DateTimeField(null=True, blank=True)
    decide_par = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    pieces_purgees_le = models.DateTimeField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-cree_le"]

    def __str__(self):
        return f"{self.nom} {self.prenoms}"

    @property
    def nom_complet(self):
        return f"{self.nom} {self.prenoms}"

    @property
    def initiales(self):
        return (self.nom[:1] + self.prenoms[:1]).upper()

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = f"INS-{timezone.now():%y%m}-{uuid.uuid4().hex[:5].upper()}"
        if self.consentement and not self.consentement_le:
            self.consentement_le = timezone.now()
        self.nom = self.nom.strip().upper()
        super().save(*args, **kwargs)

    def purger_pieces(self):
        for f in (self.fichier_piece, self.photo):
            if f:
                f.delete(save=False)
        self.pieces_purgees_le = timezone.now()
        self.save(update_fields=["fichier_piece", "photo", "pieces_purgees_le"])


# ---------------------------------------------------------------------------
# Membres
# ---------------------------------------------------------------------------
class Membre(models.Model):
    class Statut(models.TextChoices):
        ACTIF = "actif", "Actif"
        SUSPENDU = "suspendu", "Suspendu"
        CLOTURE = "cloture", "Clôturé"

    matricule = models.CharField(max_length=20, unique=True, editable=False)
    inscription = models.OneToOneField(Inscription, on_delete=models.PROTECT, related_name="membre")
    groupe = models.ForeignKey(Groupe, on_delete=models.PROTECT, related_name="membres")
    nom = models.CharField(max_length=80)
    prenoms = models.CharField("Prénoms", max_length=120)
    telephone = models.CharField("Téléphone", max_length=30)
    adresse = models.CharField(max_length=200)
    temoin_nom = models.CharField("Témoin", max_length=150)
    temoin_telephone = models.CharField("Téléphone du témoin", max_length=30)
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.ACTIF)
    motif_statut = models.TextField(blank=True)
    cloture_le = models.DateTimeField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nom", "prenoms"]

    def __str__(self):
        return f"{self.nom} {self.prenoms}"

    @property
    def nom_complet(self):
        return f"{self.nom} {self.prenoms}"

    @property
    def initiales(self):
        return (self.nom[:1] + self.prenoms[:1]).upper()

    def save(self, *args, **kwargs):
        if not self.matricule:
            self.matricule = f"MBR-{uuid.uuid4().hex[:6].upper()}"
        super().save(*args, **kwargs)

    # --- Calculs vérifiables -------------------------------------------------
    def total_verse(self):
        return Cotisation.valides().filter(membre=self).aggregate(s=Sum("montant"))["s"] or 0

    def jours_payes(self):
        return Cotisation.valides().filter(membre=self).aggregate(s=Sum("nb_jours"))["s"] or 0

    def attendu(self, jour=None):
        return self.groupe.jours_ecoules(jour) * self.groupe.formule.cotisation_journaliere

    def ecart(self, jour=None):
        """Positif = retard (montant dû), négatif = avance."""
        return self.attendu(jour) - self.total_verse()

    def jours_de_retard(self, jour=None):
        return max(self.groupe.jours_ecoules(jour) - self.jours_payes(), 0)

    @property
    def tour(self):
        return self.tours.first()


# ---------------------------------------------------------------------------
# Cotisations
# ---------------------------------------------------------------------------
class Cotisation(models.Model):
    class Mode(models.TextChoices):
        WAVE = "wave", "Wave"
        ESPECES = "especes", "Espèces"

    reference = models.CharField("Réf. opération", max_length=24, unique=True, editable=False)
    numero_recu = models.CharField("N° de reçu", max_length=24, unique=True, editable=False)
    membre = models.ForeignKey(Membre, on_delete=models.PROTECT, related_name="cotisations")
    groupe = models.ForeignKey(Groupe, on_delete=models.PROTECT, related_name="cotisations")
    nb_jours = models.PositiveSmallIntegerField("Nombre de jours couverts", default=1,
                                                validators=[MinValueValidator(1), MaxValueValidator(100)])
    montant = models.PositiveIntegerField("Montant (FCFA)")
    date_paiement = models.DateField("Date du paiement")
    mode = models.CharField(max_length=10, choices=Mode.choices)
    id_wave = models.CharField("ID transaction Wave", max_length=40, blank=True, null=True)
    verifie = models.BooleanField("Paiement vérifié par l'administratrice", default=False)
    note = models.CharField(max_length=200, blank=True)
    annulee = models.BooleanField(default=False)
    motif_annulation = models.TextField(blank=True)
    annulee_le = models.DateTimeField(null=True, blank=True)
    enregistre_par = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                       related_name="+")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_paiement", "-cree_le"]
        constraints = [
            models.UniqueConstraint(fields=["id_wave"], condition=Q(id_wave__isnull=False),
                                    name="id_wave_unique"),
        ]

    def __str__(self):
        return f"{self.numero_recu} — {self.membre} — {self.montant} FCFA"

    @classmethod
    def valides(cls):
        return cls.objects.filter(annulee=False)

    def clean(self):
        if self.mode == self.Mode.WAVE and not self.id_wave:
            raise ValidationError({"id_wave": "L'ID de transaction Wave est obligatoire."})
        if not self.verifie:
            raise ValidationError({"verifie": "Un paiement ne peut être enregistré qu'après vérification (§5.8)."})

    def save(self, *args, **kwargs):
        if self.mode != self.Mode.WAVE:
            self.id_wave = None
        elif self.id_wave:
            self.id_wave = self.id_wave.strip().upper()
        if not self.reference:
            self.reference = f"OP-{timezone.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
        if not self.numero_recu:
            with transaction.atomic():
                annee = timezone.localdate().year
                compteur, _ = Compteur.objects.select_for_update().get_or_create(cle=f"recu-{annee}")
                compteur.valeur += 1
                compteur.save()
                self.numero_recu = f"REC-{annee}-{compteur.valeur:05d}"
        super().save(*args, **kwargs)


class Compteur(models.Model):
    """Compteur séquentiel (numéros de reçus sans trou)."""
    cle = models.CharField(max_length=30, unique=True)
    valeur = models.PositiveIntegerField(default=0)


# ---------------------------------------------------------------------------
# Tours / Bénéficiaires
# ---------------------------------------------------------------------------
class Tour(models.Model):
    class Statut(models.TextChoices):
        PROGRAMME = "programme", "Programmé"
        DISTRIBUE = "distribue", "Distribué"

    groupe = models.ForeignKey(Groupe, on_delete=models.PROTECT, related_name="tours")
    membre = models.ForeignKey(Membre, on_delete=models.PROTECT, related_name="tours")
    rang = models.PositiveSmallIntegerField()
    date_prevue = models.DateField()
    montant = models.PositiveIntegerField("Montant prévu (FCFA)")
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.PROGRAMME)
    reference = models.CharField(max_length=24, unique=True, editable=False)
    distribue_le = models.DateTimeField(null=True, blank=True)
    mode_remise = models.CharField(max_length=10, choices=Cotisation.Mode.choices, blank=True)
    id_wave_remise = models.CharField(max_length=40, blank=True)
    reception_confirmee = models.BooleanField("Réception confirmée par le membre", default=False)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["groupe", "rang"]
        constraints = [
            models.UniqueConstraint(fields=["groupe", "rang"], name="rang_unique_par_groupe"),
        ]

    def __str__(self):
        return f"Tour {self.rang} — {self.membre}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = f"DIS-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    @property
    def en_retard(self):
        return self.statut == self.Statut.PROGRAMME and self.date_prevue < timezone.localdate()


class ChangementOrdre(models.Model):
    groupe = models.ForeignKey(Groupe, on_delete=models.PROTECT, related_name="changements_ordre")
    membre = models.ForeignKey(Membre, on_delete=models.PROTECT, related_name="+")
    ancien_rang = models.PositiveSmallIntegerField()
    nouveau_rang = models.PositiveSmallIntegerField()
    membre_echange = models.ForeignKey(Membre, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    justification = models.TextField()
    par = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-le"]


# ---------------------------------------------------------------------------
# Autres mouvements (dépenses autorisées, corrections)
# ---------------------------------------------------------------------------
class Mouvement(models.Model):
    class Type(models.TextChoices):
        DEPENSE = "depense", "Dépense autorisée"
        CORRECTION = "correction", "Correction d'opération"

    reference = models.CharField(max_length=24, unique=True, editable=False)
    type = models.CharField(max_length=12, choices=Type.choices)
    libelle = models.CharField(max_length=200)
    montant = models.IntegerField("Montant (FCFA)", help_text="Négatif pour une sortie, positif pour une entrée.")
    date = models.DateField()
    groupe = models.ForeignKey(Groupe, null=True, blank=True, on_delete=models.PROTECT, related_name="mouvements")
    operation_liee = models.CharField("Réf. opération corrigée", max_length=24, blank=True)
    par = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-cree_le"]

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = f"MVT-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Journal d'audit
# ---------------------------------------------------------------------------
class JournalAudit(models.Model):
    le = models.DateTimeField(auto_now_add=True, db_index=True)
    utilisateur = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=60, db_index=True)
    objet = models.CharField(max_length=120, blank=True)
    details = models.TextField(blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-le"]
        verbose_name = "Entrée du journal"
        verbose_name_plural = "Journal d'audit"

    def __str__(self):
        return f"{self.le:%d/%m/%Y %H:%M} — {self.action}"
