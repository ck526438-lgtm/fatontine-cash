import mimetypes
from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import exports, pdf, services
from .forms import (AnnulationForm, ChangementRangForm, CotisationForm, DistributionForm, FormuleForm,
                    GroupeForm, InscriptionForm, MembreForm, MouvementForm, ParametresForm,
                    RefusInscriptionForm, StatutMembreForm, ValidationInscriptionForm)
from .models import (ChangementOrdre, Cotisation, Formule, Groupe, Inscription, JournalAudit, Membre,
                     Mouvement, Parametres, Tour)


def _erreur(request, exc):
    for m in (exc.messages if hasattr(exc, "messages") else [str(exc)]):
        messages.error(request, m)


def sante(request):
    return JsonResponse({"statut": "ok", "heure": timezone.now().isoformat()})


def error_404(request, exception):
    return render(request, "tontine/404.html", status=404)


# ---------------------------------------------------------------------------
# Tableau de bord
# ---------------------------------------------------------------------------
def dashboard(request):
    services.synchroniser_statuts_groupes()
    groupe_id = request.GET.get("groupe") or request.session.get("groupe_actif")
    groupe = Groupe.objects.filter(pk=groupe_id).first() if groupe_id and groupe_id != "tous" else None
    request.session["groupe_actif"] = str(groupe.pk) if groupe else "tous"

    cot = Cotisation.valides()
    tours = Tour.objects.select_related("membre", "groupe")
    membres = Membre.objects.exclude(statut=Membre.Statut.CLOTURE)
    if groupe:
        cot, tours, membres = cot.filter(groupe=groupe), tours.filter(groupe=groupe), membres.filter(groupe=groupe)
    t = services.totaux(cot, tours, Mouvement.objects.filter(groupe=groupe) if groupe else None)
    retards = services.membres_en_retard(groupe)

    gauge_groupe = groupe or Groupe.objects.filter(statut=Groupe.Statut.EN_COURS).first()
    context = {
        "groupes": Groupe.objects.all(),
        "groupe": groupe,
        "totaux": t,
        "nb_membres": membres.count(),
        "nb_groupes_actifs": Groupe.objects.filter(statut__in=[Groupe.Statut.OUVERT, Groupe.Statut.EN_COURS]).count(),
        "nb_retards": len(retards),
        "montant_retards": sum(r["ecart"] for r in retards),
        "prochains": tours.filter(statut=Tour.Statut.PROGRAMME).order_by("date_prevue")[:4],
        "dernieres": cot.select_related("membre", "groupe")[:5],
        "serie": services.serie_journaliere(14, groupe),
        "gauge_groupe": gauge_groupe,
        "vierge": not Groupe.objects.exists() and not Inscription.objects.exists(),
    }
    return render(request, "tontine/dashboard.html", context)


# ---------------------------------------------------------------------------
# Inscriptions
# ---------------------------------------------------------------------------
def inscriptions(request):
    statut = request.GET.get("statut", Inscription.Statut.EN_ATTENTE)
    qs = Inscription.objects.select_related("formule_souhaitee")
    if statut != "tous":
        qs = qs.filter(statut=statut)
    compteurs = dict(Inscription.objects.values_list("statut").annotate(n=Count("id")))
    return render(request, "tontine/inscriptions.html", {
        "inscriptions": qs, "statut": statut, "compteurs": compteurs, "statuts": Inscription.Statut.choices,
    })


def inscription_nouvelle(request):
    if not Formule.objects.filter(active=True).exists():
        messages.error(request, "Aucune formule active : configurez d'abord les formules.")
        return redirect("formules")
    form = InscriptionForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        ins = form.save()
        services.journaliser(request, "inscription.creee", ins.reference, ins.nom_complet)
        messages.success(request, f"Dossier {ins.reference} enregistré : en attente de votre validation.")
        return redirect("inscription_detail", pk=ins.pk)
    return render(request, "tontine/inscription_form.html", {"form": form})


def inscription_detail(request, pk):
    ins = get_object_or_404(Inscription.objects.select_related("formule_souhaitee", "decide_par"), pk=pk)
    ctx = {"ins": ins}
    if ins.statut == Inscription.Statut.EN_ATTENTE:
        ctx["form_valider"] = ValidationInscriptionForm(ins)
        ctx["form_refuser"] = RefusInscriptionForm()
    elif ins.statut == Inscription.Statut.REFUSEE:
        ctx["wa_refus"] = services.lien_whatsapp(ins.telephone, services.message_refus(ins))
    elif hasattr(ins, "membre"):
        ctx["wa_validation"] = services.lien_whatsapp(ins.telephone, services.message_validation(ins.membre))
    return render(request, "tontine/inscription_detail.html", ctx)


@require_POST
def inscription_valider(request, pk):
    ins = get_object_or_404(Inscription, pk=pk)
    form = ValidationInscriptionForm(ins, request.POST)
    if form.is_valid():
        try:
            m = services.valider_inscription(request, ins, form.cleaned_data["groupe"],
                                             form.cleaned_data["observation"])
            messages.success(request, f"{m.nom_complet} validé(e) et intégré(e) au groupe {m.groupe}.")
        except ValidationError as e:
            _erreur(request, e)
    else:
        for errs in form.errors.values():
            for e in errs:
                messages.error(request, e)
    return redirect("inscription_detail", pk=pk)


@require_POST
def inscription_refuser(request, pk):
    ins = get_object_or_404(Inscription, pk=pk)
    form = RefusInscriptionForm(request.POST)
    if form.is_valid():
        try:
            services.refuser_inscription(request, ins, form.cleaned_data["motif"])
            messages.info(request, "Dossier refusé. Pensez à prévenir la personne.")
        except ValidationError as e:
            _erreur(request, e)
    return redirect("inscription_detail", pk=pk)


def piece_privee(request, pk, champ):
    """Sert une pièce d'identité uniquement à l'administratrice connectée (§8)."""
    if champ not in ("fichier_piece", "photo"):
        raise Http404
    ins = get_object_or_404(Inscription, pk=pk)
    f = getattr(ins, champ)
    if not f:
        raise Http404
    services.journaliser(request, "piece.consultee", ins.reference, champ)
    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
    resp = FileResponse(f.open("rb"), content_type=ctype)
    resp["Content-Disposition"] = f'inline; filename="{ins.reference}-{champ}{f.name[f.name.rfind("."):]}"'
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "private, no-store"
    return resp


# ---------------------------------------------------------------------------
# Groupes & formules
# ---------------------------------------------------------------------------
def groupes(request):
    services.synchroniser_statuts_groupes()
    return render(request, "tontine/groupes.html", {
        "groupes": Groupe.objects.select_related("formule").annotate(
            nb=Count("membres", filter=~Q(membres__statut=Membre.Statut.CLOTURE))),
        "formules": Formule.objects.filter(active=True),
    })


def groupe_form(request, pk=None):
    instance = get_object_or_404(Groupe, pk=pk) if pk else None
    form = GroupeForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        g = form.save()
        g.recalculer_dates_tours()
        services.journaliser(request, "groupe.modifie" if pk else "groupe.cree", g.nom,
                             f"{g.formule} — démarrage {g.date_demarrage:%d/%m/%Y}")
        messages.success(request, f"Groupe « {g.nom} » enregistré.")
        return redirect("groupe_detail", pk=g.pk)
    return render(request, "tontine/groupe_form.html", {"form": form, "groupe": instance})


def groupe_detail(request, pk):
    g = get_object_or_404(Groupe.objects.select_related("formule"), pk=pk)
    membres = []
    for m in g.membres.all():
        membres.append({"m": m, "verse": m.total_verse(), "ecart": m.ecart(), "tour": m.tour})
    return render(request, "tontine/groupe_detail.html", {
        "g": g, "membres": membres, "tours": g.tours.select_related("membre"),
        "collecte": g.collecte(), "distribue": g.distribue(),
        "theorique_tour": g.formule.collecte_theorique(g.capacite, g.jours_par_tour),
        "theorique_cycle": g.formule.collecte_theorique(g.capacite, g.jours_par_tour) * g.capacite,
    })


def formules(request):
    pk = request.GET.get("modifier")
    instance = get_object_or_404(Formule, pk=pk) if pk else None
    form = FormuleForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        f = form.save()
        services.journaliser(request, "formule.enregistree", f.code, str(f))
        messages.success(request, f"Formule {f.code} enregistrée.")
        return redirect("formules")
    return render(request, "tontine/formules.html", {
        "formules": Formule.objects.all(), "form": form, "instance": instance,
    })


# ---------------------------------------------------------------------------
# Membres
# ---------------------------------------------------------------------------
def membres(request):
    q = request.GET.get("q", "").strip()
    statut = request.GET.get("statut", "")
    qs = Membre.objects.select_related("groupe__formule")
    if q:
        qs = qs.filter(Q(nom__icontains=q) | Q(prenoms__icontains=q) | Q(telephone__icontains=q) |
                       Q(matricule__icontains=q))
    if statut:
        qs = qs.filter(statut=statut)
    lignes = [{"m": m, "verse": m.total_verse(), "ecart": m.ecart()} for m in qs]
    return render(request, "tontine/membres.html", {"lignes": lignes, "q": q, "statut": statut,
                                                    "statuts": Membre.Statut.choices})


def membre_detail(request, pk):
    m = get_object_or_404(Membre.objects.select_related("groupe__formule", "inscription"), pk=pk)
    ctx = {
        "m": m, "cotisations": m.cotisations.all(), "verse": m.total_verse(), "attendu": m.attendu(),
        "ecart": m.ecart(), "jours_retard": m.jours_de_retard(), "tour": m.tour,
        "form_statut": StatutMembreForm(initial={"statut": m.statut}),
        "wa_rappel": services.lien_whatsapp(m.telephone, services.message_rappel(m)),
    }
    if ctx["ecart"] > 0:
        ctx["wa_retard"] = services.lien_whatsapp(m.telephone, services.message_retard(m, ctx["ecart"]))
    return render(request, "tontine/membre_detail.html", ctx)


def membre_modifier(request, pk):
    m = get_object_or_404(Membre, pk=pk)
    form = MembreForm(request.POST or None, instance=m)
    if request.method == "POST" and form.is_valid():
        champs = ", ".join(form.changed_data) or "aucun"
        form.save()
        services.journaliser(request, "membre.modifie", m.matricule, f"Champs modifiés : {champs}")
        messages.success(request, "Informations mises à jour.")
        return redirect("membre_detail", pk=pk)
    return render(request, "tontine/simple_form.html", {
        "form": form, "titre": f"Modifier — {m.nom_complet}", "retour": f"/membres/{m.pk}/",
    })


@require_POST
def membre_statut(request, pk):
    m = get_object_or_404(Membre, pk=pk)
    form = StatutMembreForm(request.POST)
    if form.is_valid():
        ancien = m.get_statut_display()
        m.statut = form.cleaned_data["statut"]
        m.motif_statut = form.cleaned_data["motif"]
        m.cloture_le = timezone.now() if m.statut == Membre.Statut.CLOTURE else None
        m.save()
        services.journaliser(request, "membre.statut", m.matricule,
                             f"{ancien} → {m.get_statut_display()} : {m.motif_statut}")
        messages.success(request, f"Statut de {m.nom_complet} : {m.get_statut_display()}.")
    else:
        messages.error(request, "Le motif est obligatoire pour changer le statut.")
    return redirect("membre_detail", pk=pk)


# ---------------------------------------------------------------------------
# Cotisations & reçus
# ---------------------------------------------------------------------------
def cotisations(request):
    services.synchroniser_statuts_groupes()
    initial = {}
    if request.GET.get("membre"):
        initial["membre"] = request.GET["membre"]
    form = CotisationForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            c = services.enregistrer_cotisation(request, form.save(commit=False))
            messages.success(request, f"Paiement enregistré — reçu {c.numero_recu}.")
            return redirect("recu", pk=c.pk)
        except ValidationError as e:
            _erreur(request, e)

    qs = Cotisation.objects.select_related("membre", "groupe")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(numero_recu__icontains=q) | Q(reference__icontains=q) | Q(id_wave__icontains=q) |
                       Q(membre__nom__icontains=q))
    tarifs = {m.pk: m.groupe.formule.cotisation_journaliere for m in form.fields["membre"].queryset}
    return render(request, "tontine/cotisations.html", {
        "form": form, "cotisations": qs[:200], "q": q, "retards": services.membres_en_retard()[:12],
        "total": Cotisation.valides().aggregate(s=Sum("montant"))["s"] or 0, "tarifs": tarifs,
        "aucun_membre": not form.fields["membre"].queryset.exists(),
    })


def recu(request, pk):
    c = get_object_or_404(Cotisation.objects.select_related("membre", "groupe__formule"), pk=pk)
    return render(request, "tontine/recu.html", {
        "c": c, "wa": services.lien_whatsapp(c.membre.telephone, services.message_recu(c)),
        "form_annuler": AnnulationForm(),
    })


def recu_pdf(request, pk):
    c = get_object_or_404(Cotisation.objects.select_related("membre", "groupe__formule"), pk=pk)
    resp = HttpResponse(pdf.recu(c), content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{c.numero_recu}.pdf"'
    return resp


@require_POST
def cotisation_annuler(request, pk):
    c = get_object_or_404(Cotisation, pk=pk)
    form = AnnulationForm(request.POST)
    if form.is_valid():
        try:
            services.annuler_cotisation(request, c, form.cleaned_data["motif"])
            messages.info(request, f"Opération {c.reference} annulée et tracée.")
        except ValidationError as e:
            _erreur(request, e)
    else:
        messages.error(request, "Le motif d'annulation est obligatoire.")
    return redirect("recu", pk=pk)


# ---------------------------------------------------------------------------
# Bénéficiaires
# ---------------------------------------------------------------------------
def beneficiaires(request):
    gid = request.GET.get("groupe", "")
    tours = Tour.objects.select_related("membre", "groupe").order_by("date_prevue", "rang")
    if gid:
        tours = tours.filter(groupe_id=gid)
    changements = ChangementOrdre.objects.select_related("membre", "membre_echange", "groupe")
    if gid:
        changements = changements.filter(groupe_id=gid)
    return render(request, "tontine/beneficiaires.html", {
        "tours": tours, "groupes": Groupe.objects.all(), "gid": gid, "changements": changements[:30],
        "aujourd_hui": timezone.localdate(),
    })


def tour_detail(request, pk):
    t = get_object_or_404(Tour.objects.select_related("membre", "groupe"), pk=pk)
    form_rang = ChangementRangForm(t, request.POST if request.POST.get("action") == "rang" else None,
                                   initial={"nouveau_rang": t.rang})
    form_dist = DistributionForm(request.POST if request.POST.get("action") == "distribuer" else None)
    if request.method == "POST":
        try:
            if request.POST.get("action") == "rang" and form_rang.is_valid():
                services.changer_rang(request, t, form_rang.cleaned_data["nouveau_rang"],
                                      form_rang.cleaned_data["justification"])
                messages.success(request, "Ordre de passage modifié et justifié.")
                return redirect("tour_detail", pk=pk)
            if request.POST.get("action") == "distribuer" and form_dist.is_valid():
                d = form_dist.cleaned_data
                services.enregistrer_distribution(request, t, d["mode"], d["id_wave"], d["note"], d["confirmee"])
                messages.success(request, f"Distribution de {services.fcfa(t.montant)} enregistrée.")
                return redirect("tour_detail", pk=pk)
        except ValidationError as e:
            _erreur(request, e)
    if request.POST.get("action") == "confirmer" and t.statut == Tour.Statut.DISTRIBUE:
        t.reception_confirmee = True
        t.save(update_fields=["reception_confirmee"])
        services.journaliser(request, "tour.reception_confirmee", t.reference, t.membre.nom_complet)
        messages.success(request, "Réception confirmée.")
        return redirect("tour_detail", pk=pk)
    return render(request, "tontine/tour_detail.html", {
        "t": t, "form_rang": form_rang, "form_dist": form_dist,
        "wa": services.lien_whatsapp(t.membre.telephone, services.message_distribution(t)),
        "historique": ChangementOrdre.objects.filter(Q(membre=t.membre) | Q(membre_echange=t.membre)),
    })


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------
def transactions(request):
    form = MouvementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        mv = form.save(commit=False)
        mv.par = request.user
        mv.save()
        services.journaliser(request, "mouvement.cree", mv.reference, f"{mv.get_type_display()} : {mv.libelle} ({mv.montant})")
        messages.success(request, "Mouvement enregistré.")
        return redirect("transactions")
    lignes = []
    for c in Cotisation.objects.select_related("membre", "groupe")[:150]:
        lignes.append({"date": c.date_paiement, "type": "Cotisation" + (" (annulée)" if c.annulee else ""),
                       "libelle": f"{c.membre.nom_complet} — {c.groupe}", "ref": c.reference,
                       "extra": c.id_wave or c.get_mode_display(), "montant": 0 if c.annulee else c.montant,
                       "url": f"/cotisations/{c.pk}/recu/"})
    for t in Tour.objects.filter(statut=Tour.Statut.DISTRIBUE).select_related("membre", "groupe"):
        lignes.append({"date": timezone.localtime(t.distribue_le).date(), "type": "Distribution",
                       "libelle": f"{t.membre.nom_complet} — tour {t.rang} ({t.groupe})", "ref": t.reference,
                       "extra": t.id_wave_remise or t.get_mode_remise_display(), "montant": -t.montant,
                       "url": f"/beneficiaires/{t.pk}/"})
    for mv in Mouvement.objects.select_related("groupe"):
        lignes.append({"date": mv.date, "type": mv.get_type_display(), "libelle": mv.libelle, "ref": mv.reference,
                       "extra": mv.operation_liee, "montant": mv.montant if mv.type == Mouvement.Type.DEPENSE else 0,
                       "info": mv.montant if mv.type == Mouvement.Type.CORRECTION else None, "url": ""})
    lignes.sort(key=lambda l: l["date"], reverse=True)
    return render(request, "tontine/transactions.html", {"lignes": lignes, "totaux": services.totaux(), "form": form})


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
def notifications(request):
    services.synchroniser_statuts_groupes()
    aujourd_hui = timezone.localdate()
    payes_auj = set(Cotisation.valides().filter(date_paiement=aujourd_hui).values_list("membre_id", flat=True))
    rappels = [m for m in Membre.objects.filter(statut=Membre.Statut.ACTIF, groupe__statut=Groupe.Statut.EN_COURS)
               .select_related("groupe__formule") if m.pk not in payes_auj]
    items = {
        "retards": [{"m": r["membre"], "ecart": r["ecart"],
                     "wa": services.lien_whatsapp(r["membre"].telephone, services.message_retard(r["membre"], r["ecart"]))}
                    for r in services.membres_en_retard()],
        "rappels": [{"m": m, "wa": services.lien_whatsapp(m.telephone, services.message_rappel(m))} for m in rappels],
        "distributions": [{"t": t, "wa": services.lien_whatsapp(t.membre.telephone, services.message_distribution(t))}
                          for t in Tour.objects.filter(statut=Tour.Statut.PROGRAMME,
                                                       date_prevue__lte=aujourd_hui + timedelta(days=3))
                          .select_related("membre", "groupe").order_by("date_prevue")],
        "validations": [{"i": i, "wa": services.lien_whatsapp(i.telephone, services.message_validation(i.membre))}
                        for i in Inscription.objects.filter(statut=Inscription.Statut.VALIDEE,
                                                            decide_le__gte=timezone.now() - timedelta(days=7))
                        .select_related("membre__groupe__formule")],
        "refus": [{"i": i, "wa": services.lien_whatsapp(i.telephone, services.message_refus(i))}
                  for i in Inscription.objects.filter(statut=Inscription.Statut.REFUSEE,
                                                      decide_le__gte=timezone.now() - timedelta(days=7))],
    }
    return render(request, "tontine/notifications.html", items)


# ---------------------------------------------------------------------------
# Rapports
# ---------------------------------------------------------------------------
def _parse_date(s, defaut):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return defaut


def rapports(request):
    type_p = request.GET.get("periode", "jour")
    ref = _parse_date(request.GET.get("date"), timezone.localdate())
    debut, fin = services.periode(type_p, ref)
    data = exports.donnees_periode(debut, fin)
    return render(request, "tontine/rapports.html", {
        "type_p": type_p, "ref": ref, "debut": debut, "fin": fin, "data": data,
        "groupes": Groupe.objects.select_related("formule"),
        "bilan_groupes": exports.bilan_groupes(),
        "membres": Membre.objects.all(),
    })


def export(request, rapport, fmt):
    if fmt not in ("xlsx", "pdf"):
        raise Http404
    type_p = request.GET.get("periode", "jour")
    ref = _parse_date(request.GET.get("date"), timezone.localdate())
    try:
        titre, entetes, lignes, resume = exports.construire(rapport, request.GET, type_p, ref)
    except KeyError:
        raise Http404
    nom = f"FATONTINE_{rapport}_{timezone.localdate():%Y%m%d}"
    services.journaliser(request, "rapport.exporte", rapport, fmt)
    if fmt == "xlsx":
        resp = HttpResponse(exports.vers_xlsx(titre, entetes, lignes, resume),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="{nom}.xlsx"'
    else:
        resp = HttpResponse(pdf.rapport(titre, entetes, lignes, resume), content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{nom}.pdf"'
    return resp


# ---------------------------------------------------------------------------
# Paramètres & journal
# ---------------------------------------------------------------------------
def parametres(request):
    p = Parametres.get()
    form = ParametresForm(request.POST or None, instance=p)
    if request.method == "POST" and form.is_valid():
        champs = ", ".join(f"{c}={form.cleaned_data[c]}" for c in form.changed_data)
        form.save()
        services.journaliser(request, "parametres.modifies", "Paramètres", champs)
        messages.success(request, "Paramètres enregistrés.")
        return redirect("parametres")
    return render(request, "tontine/parametres.html", {"form": form, "p": p, "formules": Formule.objects.all()})


def journal(request):
    qs = JournalAudit.objects.select_related("utilisateur")
    action = request.GET.get("action", "")
    if action:
        qs = qs.filter(action__startswith=action)
    return render(request, "tontine/journal.html", {"entrees": qs[:300], "action": action})
