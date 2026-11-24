"""Règles métier : toutes les opérations sensibles passent par ici (transactions + audit)."""
from datetime import date, timedelta
from urllib.parse import quote

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (ChangementOrdre, Cotisation, Groupe, Inscription, JournalAudit, Membre,
                     Mouvement, Parametres, Tour)


def fcfa(n) -> str:
    return f"{int(round(n or 0)):,}".replace(",", " ") + " FCFA"


def client_ip(request):
    if request is None:
        return None
    fwd = request.META.get("HTTP_X_FORWARDED_FOR")
    return (fwd.split(",")[0].strip() if fwd else request.META.get("REMOTE_ADDR")) or None


def journaliser(request, action, objet="", details=""):
    JournalAudit.objects.create(
        utilisateur=request.user if request and request.user.is_authenticated else None,
        action=action, objet=str(objet)[:120], details=details, ip=client_ip(request),
    )


# ---------------------------------------------------------------------------
# Inscriptions
# ---------------------------------------------------------------------------
@transaction.atomic
def valider_inscription(request, inscription: Inscription, groupe: Groupe, observation="") -> Membre:
    inscription = Inscription.objects.select_for_update().get(pk=inscription.pk)
    groupe = Groupe.objects.select_for_update().get(pk=groupe.pk)
    if inscription.statut != Inscription.Statut.EN_ATTENTE:
        raise ValidationError("Ce dossier a déjà été traité.")
    if not inscription.consentement:
        raise ValidationError("Le consentement ARTCI du membre est manquant.")
    if groupe.statut in (Groupe.Statut.TERMINE, Groupe.Statut.SUSPENDU):
        raise ValidationError("Ce groupe n'accepte plus de membres.")
    if groupe.est_complet:
        raise ValidationError(f"Le groupe « {groupe} » est complet ({groupe.capacite} places).")

    membre = Membre.objects.create(
        inscription=inscription, groupe=groupe, nom=inscription.nom, prenoms=inscription.prenoms,
        telephone=inscription.telephone, adresse=inscription.adresse,
        temoin_nom=inscription.temoin_nom, temoin_telephone=inscription.temoin_telephone,
    )
    rangs_pris = set(groupe.tours.values_list("rang", flat=True))
    rang = next(r for r in range(1, groupe.capacite + 1) if r not in rangs_pris)
    Tour.objects.create(
        groupe=groupe, membre=membre, rang=rang, date_prevue=groupe.date_tour(rang),
        montant=groupe.formule.reception_annoncee,
    )
    inscription.statut = Inscription.Statut.VALIDEE
    inscription.motif_decision = observation
    inscription.decide_le = timezone.now()
    inscription.decide_par = request.user
    inscription.save()
    journaliser(request, "inscription.validee", inscription.reference,
                f"{membre.nom_complet} intégré(e) au groupe {groupe} — tour n°{rang}.")
    return membre


@transaction.atomic
def refuser_inscription(request, inscription: Inscription, motif: str):
    if inscription.statut != Inscription.Statut.EN_ATTENTE:
        raise ValidationError("Ce dossier a déjà été traité.")
    if not motif.strip():
        raise ValidationError("Le motif du refus est obligatoire.")
    inscription.statut = Inscription.Statut.REFUSEE
    inscription.motif_decision = motif.strip()
    inscription.decide_le = timezone.now()
    inscription.decide_par = request.user
    inscription.save()
    journaliser(request, "inscription.refusee", inscription.reference, motif)


# ---------------------------------------------------------------------------
# Cotisations
# ---------------------------------------------------------------------------
@transaction.atomic
def enregistrer_cotisation(request, cotisation: Cotisation) -> Cotisation:
    cotisation.groupe = cotisation.membre.groupe
    cotisation.montant = cotisation.groupe.formule.cotisation_journaliere * cotisation.nb_jours
    cotisation.enregistre_par = request.user
    cotisation.full_clean(exclude=["reference", "numero_recu"])
    cotisation.save()
    journaliser(request, "cotisation.enregistree", cotisation.reference,
                f"{cotisation.numero_recu} — {cotisation.membre.nom_complet} — {fcfa(cotisation.montant)} "
                f"({cotisation.get_mode_display()}{' ' + cotisation.id_wave if cotisation.id_wave else ''}).")
    return cotisation


@transaction.atomic
def annuler_cotisation(request, cotisation: Cotisation, motif: str):
    if cotisation.annulee:
        raise ValidationError("Opération déjà annulée.")
    if not motif.strip():
        raise ValidationError("Le motif d'annulation est obligatoire.")
    cotisation.annulee = True
    cotisation.motif_annulation = motif.strip()
    cotisation.annulee_le = timezone.now()
    cotisation.save(update_fields=["annulee", "motif_annulation", "annulee_le"])
    Mouvement.objects.create(
        type=Mouvement.Type.CORRECTION, libelle=f"Annulation {cotisation.numero_recu} : {motif.strip()}"[:200],
        montant=-cotisation.montant, date=timezone.localdate(), groupe=cotisation.groupe,
        operation_liee=cotisation.reference, par=request.user,
    )
    journaliser(request, "cotisation.annulee", cotisation.reference, motif)


# ---------------------------------------------------------------------------
# Tours
# ---------------------------------------------------------------------------
@transaction.atomic
def changer_rang(request, tour: Tour, nouveau_rang: int, justification: str):
    groupe = tour.groupe
    if not justification.strip():
        raise ValidationError("La justification est obligatoire.")
    if not 1 <= nouveau_rang <= groupe.capacite:
        raise ValidationError(f"Le rang doit être compris entre 1 et {groupe.capacite}.")
    if tour.statut == Tour.Statut.DISTRIBUE:
        raise ValidationError("Ce tour a déjà été distribué : son rang ne peut plus changer.")
    ancien = tour.rang
    if ancien == nouveau_rang:
        raise ValidationError("Le rang choisi est identique au rang actuel.")
    autre = groupe.tours.filter(rang=nouveau_rang).first()
    if autre and autre.statut == Tour.Statut.DISTRIBUE:
        raise ValidationError("Le rang visé a déjà été distribué.")

    # Échange sans violer la contrainte d'unicité (passage par un rang temporaire)
    tour.rang = 0
    tour.save(update_fields=["rang"])
    if autre:
        autre.rang = ancien
        autre.date_prevue = groupe.date_tour(ancien)
        autre.save(update_fields=["rang", "date_prevue"])
    tour.rang = nouveau_rang
    tour.date_prevue = groupe.date_tour(nouveau_rang)
    tour.save(update_fields=["rang", "date_prevue"])

    ChangementOrdre.objects.create(
        groupe=groupe, membre=tour.membre, ancien_rang=ancien, nouveau_rang=nouveau_rang,
        membre_echange=autre.membre if autre else None, justification=justification.strip(), par=request.user,
    )
    journaliser(request, "tour.ordre_modifie", groupe,
                f"{tour.membre.nom_complet} : #{ancien} → #{nouveau_rang}"
                f"{' (échange avec ' + autre.membre.nom_complet + ')' if autre else ''}. Motif : {justification}")


@transaction.atomic
def enregistrer_distribution(request, tour: Tour, mode: str, id_wave: str = "", note: str = "",
                             confirmee: bool = False):
    if tour.statut == Tour.Statut.DISTRIBUE:
        raise ValidationError("Distribution déjà enregistrée.")
    if mode == Cotisation.Mode.WAVE and not id_wave.strip():
        raise ValidationError("L'ID de transaction Wave de la remise est obligatoire.")
    tour.statut = Tour.Statut.DISTRIBUE
    tour.distribue_le = timezone.now()
    tour.mode_remise = mode
    tour.id_wave_remise = id_wave.strip().upper()
    tour.note = note
    tour.reception_confirmee = confirmee
    tour.save()
    journaliser(request, "tour.distribue", tour.reference,
                f"{fcfa(tour.montant)} remis à {tour.membre.nom_complet} (tour n°{tour.rang}, {tour.groupe}).")


# ---------------------------------------------------------------------------
# Indicateurs
# ---------------------------------------------------------------------------
def membres_en_retard(groupe=None, jour=None):
    qs = Membre.objects.filter(statut=Membre.Statut.ACTIF).select_related("groupe__formule")
    if groupe:
        qs = qs.filter(groupe=groupe)
    resultat = []
    for m in qs:
        if m.groupe.statut != Groupe.Statut.EN_COURS:
            continue
        ecart = m.ecart(jour)
        if ecart > 0:
            resultat.append({"membre": m, "ecart": ecart, "jours": m.jours_de_retard(jour)})
    return sorted(resultat, key=lambda r: -r["ecart"])


def totaux(cotisations_qs=None, tours_qs=None, mouvements_qs=None):
    cot = (cotisations_qs if cotisations_qs is not None else Cotisation.valides())
    tours = (tours_qs if tours_qs is not None else Tour.objects.all())
    mvts = (mouvements_qs if mouvements_qs is not None else Mouvement.objects.all())
    collecte = cot.aggregate(s=Sum("montant"))["s"] or 0
    distribue = tours.filter(statut=Tour.Statut.DISTRIBUE).aggregate(s=Sum("montant"))["s"] or 0
    depenses = mvts.filter(type=Mouvement.Type.DEPENSE).aggregate(s=Sum("montant"))["s"] or 0
    part_benef = 0
    for row in cot.values("groupe").annotate(s=Sum("montant")):
        g = Groupe.objects.select_related("formule").get(pk=row["groupe"])
        part_benef += int(row["s"] * g.formule.ratio_beneficiaire)
    return {
        "collecte": collecte,
        "distribue": distribue,
        "depenses": depenses,
        "solde_caisse": collecte - distribue + depenses,
        "part_beneficiaires": part_benef,
        "part_gestion": collecte - part_benef,
    }


def serie_journaliere(jours=14, groupe=None):
    fin = timezone.localdate()
    debut = fin - timedelta(days=jours - 1)
    cot = Cotisation.valides().filter(date_paiement__range=(debut, fin))
    tours = Tour.objects.filter(statut=Tour.Statut.DISTRIBUE, distribue_le__date__range=(debut, fin))
    if groupe:
        cot, tours = cot.filter(groupe=groupe), tours.filter(groupe=groupe)
    par_jour_c = {r["date_paiement"]: r["s"] for r in cot.values("date_paiement").annotate(s=Sum("montant"))}
    par_jour_d = {}
    for t in tours:
        d = timezone.localtime(t.distribue_le).date()
        par_jour_d[d] = par_jour_d.get(d, 0) + t.montant
    serie = []
    for i in range(jours):
        d = debut + timedelta(days=i)
        serie.append({"jour": d, "cotisations": par_jour_c.get(d, 0), "distributions": par_jour_d.get(d, 0)})
    maxi = max([max(s["cotisations"], s["distributions"]) for s in serie] + [1])
    for s in serie:
        s["h_c"] = round(100 * s["cotisations"] / maxi)
        s["h_d"] = round(100 * s["distributions"] / maxi)
    return serie


def periode(type_periode: str, ref: date):
    if type_periode == "semaine":
        debut = ref - timedelta(days=ref.weekday())
        return debut, debut + timedelta(days=6)
    if type_periode == "mois":
        debut = ref.replace(day=1)
        suivant = (debut + timedelta(days=32)).replace(day=1)
        return debut, suivant - timedelta(days=1)
    return ref, ref


# ---------------------------------------------------------------------------
# Notifications WhatsApp (V1 : messages prêts à envoyer)
# ---------------------------------------------------------------------------
def numero_whatsapp(tel: str) -> str:
    chiffres = "".join(c for c in (tel or "") if c.isdigit())
    if chiffres.startswith("00"):
        chiffres = chiffres[2:]
    if len(chiffres) == 10:  # numéro ivoirien local
        chiffres = "225" + chiffres
    return chiffres


def lien_whatsapp(tel: str, message: str) -> str:
    return f"https://wa.me/{numero_whatsapp(tel)}?text={quote(message)}"


SIGNATURE = "\n\n— Fatima, FATONTINE CASH\n_La tontine, en toute simplicité._"


def message_recu(c: Cotisation) -> str:
    return (f"✅ *REÇU FATONTINE CASH*\n🧾 Reçu : *{c.numero_recu}*\n👤 {c.membre.nom_complet}\n"
            f"💰 Montant : *{fcfa(c.montant)}* ({c.nb_jours} jour{'s' if c.nb_jours > 1 else ''})\n"
            f"📅 Date : {c.date_paiement:%d/%m/%Y}\n👥 Groupe : {c.groupe}\n"
            f"🔗 Réf. : {c.reference}" + SIGNATURE)


def message_retard(m: Membre, ecart: int) -> str:
    return (f"Bonjour {m.prenoms.split(' ')[0]}, petit rappel : votre cotisation du groupe *{m.groupe}* "
            f"présente un retard de *{fcfa(ecart)}*. Merci de régulariser dès que possible." + SIGNATURE)


def message_rappel(m: Membre) -> str:
    return (f"Bonjour {m.prenoms.split(' ')[0]}, n'oubliez pas votre cotisation du jour : "
            f"*{fcfa(m.groupe.formule.cotisation_journaliere)}* (groupe {m.groupe})." + SIGNATURE)


def message_validation(m: Membre) -> str:
    t = m.tour
    return (f"Félicitations {m.prenoms.split(' ')[0]} 🎉 Votre adhésion est *validée*.\n"
            f"Groupe : *{m.groupe}* — {fcfa(m.groupe.formule.cotisation_journaliere)}/jour.\n"
            + (f"Votre tour : n°{t.rang}, prévu le {t.date_prevue:%d/%m/%Y} ({fcfa(t.montant)})." if t else "")
            + SIGNATURE)


def message_refus(i: Inscription) -> str:
    return (f"Bonjour {i.prenoms.split(' ')[0]}, après examen, votre demande d'adhésion n'a pas pu être "
            f"acceptée pour le moment. Motif : {i.motif_decision}" + SIGNATURE)


def message_distribution(t: Tour) -> str:
    return (f"Bonjour {t.membre.prenoms.split(' ')[0]}, votre réception de *{fcfa(t.montant)}* "
            f"(tour n°{t.rang}, groupe {t.groupe}) est prévue le *{t.date_prevue:%d/%m/%Y}*." + SIGNATURE)


def synchroniser_statuts_groupes(jour=None):
    """Ouvert → En cours à la date de démarrage ; En cours → Terminé quand tout est distribué."""
    jour = jour or timezone.localdate()
    Groupe.objects.filter(statut=Groupe.Statut.OUVERT, date_demarrage__lte=jour).update(
        statut=Groupe.Statut.EN_COURS)
    for g in Groupe.objects.filter(statut=Groupe.Statut.EN_COURS):
        if g.date_fin < jour and g.tours.exists() and not g.tours.filter(statut=Tour.Statut.PROGRAMME).exists():
            g.statut = Groupe.Statut.TERMINE
            g.save(update_fields=["statut"])
