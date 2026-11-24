"""Construction des rapports (§7) et export Excel."""
from io import BytesIO

from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import services
from .models import Cotisation, Groupe, Membre, Mouvement, Parametres, Tour

NAVY, GOLD = "0A192F", "D4AF37"
LIBELLES_PERIODE = {"jour": "journalier", "semaine": "hebdomadaire", "mois": "mensuel"}


def donnees_periode(debut, fin):
    cot = Cotisation.valides().filter(date_paiement__range=(debut, fin)).select_related("membre", "groupe")
    dist = Tour.objects.filter(statut=Tour.Statut.DISTRIBUE, distribue_le__date__range=(debut, fin)) \
        .select_related("membre", "groupe")
    dep = Mouvement.objects.filter(type=Mouvement.Type.DEPENSE, date__range=(debut, fin))
    annulees = Cotisation.objects.filter(annulee=True, annulee_le__date__range=(debut, fin)).count()
    collecte = cot.aggregate(s=Sum("montant"))["s"] or 0
    distribue = dist.aggregate(s=Sum("montant"))["s"] or 0
    depenses = dep.aggregate(s=Sum("montant"))["s"] or 0
    par_mode = {r["mode"]: r["s"] for r in cot.values("mode").annotate(s=Sum("montant"))}
    return {"cotisations": cot, "distributions": dist, "depenses": dep, "collecte": collecte,
            "distribue": distribue, "total_depenses": depenses, "nb": cot.count(), "annulees": annulees,
            "wave": par_mode.get("wave", 0), "especes": par_mode.get("especes", 0),
            "solde": collecte - distribue + depenses}


def bilan_groupes():
    lignes = []
    for g in Groupe.objects.select_related("formule"):
        ratio = g.formule.ratio_beneficiaire
        col = g.collecte()
        theorique = g.formule.collecte_theorique(g.capacite, g.jours_par_tour) * g.capacite
        attendu_a_date = sum(m.attendu() for m in g.membres.exclude(statut=Membre.Statut.CLOTURE))
        lignes.append({"g": g, "collecte": col, "theorique": theorique, "attendu": attendu_a_date,
                       "ecart": attendu_a_date - col, "beneficiaires": int(col * ratio),
                       "gestion": col - int(col * ratio), "distribue": g.distribue(), "progression": g.progression()})
    return lignes


def construire(rapport, params, type_p, ref):
    """Renvoie (titre, entêtes, lignes, résumé)."""
    F = services.fcfa
    if rapport == "periode":
        debut, fin = services.periode(type_p, ref)
        d = donnees_periode(debut, fin)
        titre = f"Rapport {LIBELLES_PERIODE.get(type_p, '')} du {debut:%d/%m/%Y}" + (
            f" au {fin:%d/%m/%Y}" if fin != debut else "")
        entetes = ["Date", "Type", "Référence", "Membre", "Groupe", "Mode / ID Wave", "Montant (FCFA)"]
        lignes = [[c.date_paiement.strftime("%d/%m/%Y"), "Cotisation", c.numero_recu, c.membre.nom_complet,
                   c.groupe.nom, c.id_wave or c.get_mode_display(), c.montant] for c in d["cotisations"]]
        lignes += [[timezone.localtime(t.distribue_le).strftime("%d/%m/%Y"), "Distribution", t.reference,
                    t.membre.nom_complet, t.groupe.nom, t.id_wave_remise or t.get_mode_remise_display(), -t.montant]
                   for t in d["distributions"]]
        lignes += [[m.date.strftime("%d/%m/%Y"), "Dépense", m.reference, "", m.groupe.nom if m.groupe else "",
                    m.libelle, m.montant] for m in d["depenses"]]
        resume = [("Cotisations encaissées", F(d["collecte"])), ("dont Wave", F(d["wave"])),
                  ("dont espèces", F(d["especes"])), ("Distributions", F(d["distribue"])),
                  ("Dépenses", F(d["total_depenses"])), ("Solde de la période", F(d["solde"])),
                  ("Opérations annulées", str(d["annulees"]))]
        return titre, entetes, lignes, resume

    if rapport == "groupe":
        g = get_object_or_404(Groupe, pk=params.get("groupe"))
        entetes = ["Membre", "Téléphone", "Tour", "Date tour", "Attendu", "Versé", "Écart", "Statut"]
        lignes = []
        for m in g.membres.all():
            t = m.tour
            lignes.append([m.nom_complet, m.telephone, t.rang if t else "", t.date_prevue.strftime("%d/%m/%Y") if t else "",
                           m.attendu(), m.total_verse(), m.ecart(), m.get_statut_display()])
        b = next(x for x in bilan_groupes() if x["g"].pk == g.pk)
        resume = [("Formule", str(g.formule)), ("Démarrage", g.date_demarrage.strftime("%d/%m/%Y")),
                  ("Progression", f"{b['progression']} %"), ("Collecte attendue sur le cycle", F(b["theorique"])),
                  ("Attendu à date", F(b["attendu"])), ("Collecté", F(b["collecte"])), ("Écart", F(b["ecart"])),
                  ("Distribué", F(b["distribue"])),
                  (f"Part bénéficiaires ({100 - g.formule.taux_gestion_pct:g} %)", F(b["beneficiaires"])),
                  (f"Part gestion ({g.formule.taux_gestion_pct:g} %)", F(b["gestion"]))]
        return f"Bilan du groupe {g.nom}", entetes, lignes, resume

    if rapport == "membre":
        m = get_object_or_404(Membre, pk=params.get("membre"))
        entetes = ["Date", "Reçu", "Référence", "Jours", "Mode / ID Wave", "Montant", "État"]
        lignes = [[c.date_paiement.strftime("%d/%m/%Y"), c.numero_recu, c.reference, c.nb_jours,
                   c.id_wave or c.get_mode_display(), c.montant, "Annulée" if c.annulee else "Valide"]
                  for c in m.cotisations.order_by("date_paiement")]
        t = m.tour
        resume = [("Membre", m.nom_complet), ("Matricule", m.matricule), ("Groupe", m.groupe.nom),
                  ("Attendu à date", F(m.attendu())), ("Versé", F(m.total_verse())), ("Écart", F(m.ecart())),
                  ("Tour", f"n°{t.rang} le {t.date_prevue:%d/%m/%Y} — {t.get_statut_display()}" if t else "—")]
        return f"Historique individuel — {m.nom_complet}", entetes, lignes, resume

    if rapport == "retards":
        r = services.membres_en_retard()
        entetes = ["Membre", "Téléphone", "Groupe", "Jours de retard", "Montant dû (FCFA)", "Témoin"]
        lignes = [[x["membre"].nom_complet, x["membre"].telephone, x["membre"].groupe.nom, x["jours"], x["ecart"],
                   f"{x['membre'].temoin_nom} ({x['membre'].temoin_telephone})"] for x in r]
        resume = [("Membres en retard", str(len(r))), ("Total dû", F(sum(x["ecart"] for x in r)))]
        return f"Rapport des retards au {timezone.localdate():%d/%m/%Y}", entetes, lignes, resume

    if rapport == "distributions":
        tours = Tour.objects.select_related("membre", "groupe").order_by("date_prevue")
        entetes = ["Groupe", "Tour", "Bénéficiaire", "Date prévue", "Montant", "Statut", "Remis le", "Mode / ID",
                   "Réception confirmée"]
        lignes = [[t.groupe.nom, t.rang, t.membre.nom_complet, t.date_prevue.strftime("%d/%m/%Y"), t.montant,
                   t.get_statut_display(),
                   timezone.localtime(t.distribue_le).strftime("%d/%m/%Y") if t.distribue_le else "",
                   t.id_wave_remise or t.get_mode_remise_display(), "Oui" if t.reception_confirmee else "Non"]
                  for t in tours]
        dist = tours.filter(statut=Tour.Statut.DISTRIBUE)
        resume = [("Tours programmés", str(tours.count() - dist.count())), ("Tours distribués", str(dist.count())),
                  ("Total distribué", F(dist.aggregate(s=Sum("montant"))["s"] or 0))]
        return "Bilan des distributions", entetes, lignes, resume

    if rapport == "membres":
        entetes = ["Matricule", "Nom", "Prénoms", "Téléphone", "Adresse", "Groupe", "Statut", "Pièce", "Témoin",
                   "Tél. témoin", "Total versé"]
        lignes = [[m.matricule, m.nom, m.prenoms, m.telephone, m.adresse, m.groupe.nom, m.get_statut_display(),
                   f"{m.inscription.get_type_piece_display()} {m.inscription.numero_piece}", m.temoin_nom,
                   m.temoin_telephone, m.total_verse()]
                  for m in Membre.objects.select_related("groupe", "inscription")]
        return "Répertoire des membres et témoins", entetes, lignes, [("Membres", str(len(lignes)))]

    if rapport == "cotisations":
        entetes = ["Reçu", "Référence", "Date", "Membre", "Groupe", "Jours", "Mode", "ID Wave", "Montant", "État"]
        lignes = [[c.numero_recu, c.reference, c.date_paiement.strftime("%d/%m/%Y"), c.membre.nom_complet,
                   c.groupe.nom, c.nb_jours, c.get_mode_display(), c.id_wave or "", c.montant,
                   "Annulée" if c.annulee else "Valide"]
                  for c in Cotisation.objects.select_related("membre", "groupe").order_by("date_paiement")]
        total = Cotisation.valides().aggregate(s=Sum("montant"))["s"] or 0
        return "Journal complet des cotisations", entetes, lignes, [("Total valide", F(total))]

    raise KeyError(rapport)


def vers_xlsx(titre, entetes, lignes, resume) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Rapport"
    ws["A1"] = "FATONTINE CASH"
    ws["A1"].font = Font(bold=True, size=16, color=NAVY)
    ws["A2"] = titre
    ws["A2"].font = Font(bold=True, size=12, color="B08D22")
    ws["A3"] = f"Généré le {timezone.localtime():%d/%m/%Y à %H:%M} — Devise : FCFA"
    ws["A3"].font = Font(italic=True, size=9, color="64748B")

    row = 5
    for libelle, valeur in resume:
        ws.cell(row=row, column=1, value=libelle).font = Font(bold=True, color=NAVY)
        ws.cell(row=row, column=2, value=valeur)
        row += 1
    row += 1
    thin = Side(style="thin", color="CBD5E1")
    for col, e in enumerate(entetes, 1):
        c = ws.cell(row=row, column=col, value=e)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = ws.cell(row=row + 1, column=1)
    for i, ligne in enumerate(lignes):
        for col, v in enumerate(ligne, 1):
            c = ws.cell(row=row + 1 + i, column=col, value=v)
            c.border = Border(bottom=thin)
            if isinstance(v, int):
                c.number_format = '#,##0" F";[Red]-#,##0" F"'
            if i % 2:
                c.fill = PatternFill("solid", fgColor="F8FAFC")
    for col in range(1, len(entetes) + 1):
        largeur = max([len(str(entetes[col - 1]))] + [len(str(l[col - 1])) for l in lignes[:500]] + [10])
        ws.column_dimensions[get_column_letter(col)].width = min(largeur + 3, 45)
    ws.column_dimensions["A"].width = max(ws.column_dimensions["A"].width, 28)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
