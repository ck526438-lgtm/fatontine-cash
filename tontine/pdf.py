"""Génération PDF (reçus §5.9 et rapports §7) avec ReportLab."""
from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, A5, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .services import fcfa

NAVY = colors.HexColor("#0A192F")
GOLD = colors.HexColor("#D4AF37")
GOLD_DARK = colors.HexColor("#B08D22")
SLATE = colors.HexColor("#64748B")
PEARL = colors.HexColor("#F5F7FB")


def _logo(c, x, y, r=9 * mm):
    """Logo FATONTINE CASH : pièce dorée sur fond bleu nuit."""
    c.setFillColor(NAVY)
    c.roundRect(x, y, 2 * r, 2 * r, 4 * mm, fill=1, stroke=0)
    c.setFillColor(GOLD)
    c.circle(x + r, y + r, r * 0.62, fill=1, stroke=0)
    c.setFillColor(NAVY)
    c.circle(x + r, y + r, r * 0.42, fill=1, stroke=0)
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", r * 0.62)
    c.drawCentredString(x + r, y + r - r * 0.22, "F")


def recu(cot) -> bytes:
    buf = BytesIO()
    from reportlab.pdfgen.canvas import Canvas

    W, H = A5
    c = Canvas(buf, pagesize=A5)
    c.setTitle(f"Reçu {cot.numero_recu}")
    c.setAuthor("FATONTINE CASH")

    # Bandeau
    c.setFillColor(NAVY)
    c.rect(0, H - 34 * mm, W, 34 * mm, fill=1, stroke=0)
    c.setFillColor(GOLD)
    c.rect(0, H - 35.5 * mm, W, 1.5 * mm, fill=1, stroke=0)
    _logo(c, 12 * mm, H - 27 * mm)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(34 * mm, H - 17 * mm, "FATONTINE")
    c.setFillColor(GOLD)
    c.drawString(34 * mm + c.stringWidth("FATONTINE ", "Helvetica-Bold", 16), H - 17 * mm, "CASH")
    c.setFillColor(colors.HexColor("#FBF0C9"))
    c.setFont("Helvetica", 8)
    c.drawString(34 * mm, H - 22.5 * mm, "La tontine, en toute simplicité.")
    c.setFillColor(GOLD)
    c.setFont("Helvetica-Bold", 8)
    c.drawRightString(W - 12 * mm, H - 15 * mm, "REÇU OFFICIEL")
    c.setFillColor(colors.white)
    c.setFont("Courier-Bold", 10)
    c.drawRightString(W - 12 * mm, H - 21 * mm, cot.numero_recu)

    # Montant
    y = H - 64 * mm
    c.setFillColor(PEARL)
    c.roundRect(12 * mm, y, W - 24 * mm, 22 * mm, 4 * mm, fill=1, stroke=0)
    c.setFillColor(SLATE)
    c.setFont("Helvetica-Bold", 7)
    c.drawCentredString(W / 2, y + 16 * mm, "MONTANT ENCAISSÉ")
    c.setFillColor(NAVY if not cot.annulee else colors.red)
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(W / 2, y + 6.5 * mm, fcfa(cot.montant))

    # Détails
    lignes = [
        ("Membre", cot.membre.nom_complet),
        ("Téléphone", cot.membre.telephone),
        ("Groupe", cot.groupe.nom),
        ("Formule", f"{fcfa(cot.groupe.formule.cotisation_journaliere)}/jour → {fcfa(cot.groupe.formule.reception_annoncee)}"),
        ("Jours couverts", str(cot.nb_jours)),
        ("Date du paiement", cot.date_paiement.strftime("%d/%m/%Y")),
        ("Mode", cot.get_mode_display() + (f" — ID {cot.id_wave}" if cot.id_wave else "")),
        ("Réf. opération", cot.reference),
    ]
    y -= 10 * mm
    for libelle, valeur in lignes:
        c.setFillColor(SLATE)
        c.setFont("Helvetica", 8.5)
        c.drawString(14 * mm, y, libelle)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawRightString(W - 14 * mm, y, valeur[:60])
        c.setStrokeColor(colors.HexColor("#E2E8F0"))
        c.setDash(1, 2)
        c.line(14 * mm, y - 2.5 * mm, W - 14 * mm, y - 2.5 * mm)
        c.setDash()
        y -= 8 * mm

    # Tampon
    cx, cy = W - 30 * mm, 30 * mm
    c.saveState()
    c.translate(cx, cy)
    c.rotate(-12)
    couleur = colors.red if cot.annulee else GOLD_DARK
    c.setStrokeColor(couleur)
    c.setLineWidth(1.6)
    c.circle(0, 0, 13 * mm, fill=0)
    c.setFillColor(couleur)
    c.setFont("Helvetica-Bold", 8)
    c.drawCentredString(0, 1.5 * mm, "ANNULÉ" if cot.annulee else "CERTIFIÉ")
    c.drawCentredString(0, -3.5 * mm, "" if cot.annulee else "PAYÉ")
    c.restoreState()

    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(14 * mm, 34 * mm, "Paiement vérifié par l'administratrice : Fatima")
    c.setFillColor(SLATE)
    c.setFont("Helvetica", 7.5)
    c.drawString(14 * mm, 29 * mm, "Côte d'Ivoire — Devise : FCFA")
    if cot.annulee:
        c.setFillColor(colors.red)
        c.drawString(14 * mm, 24 * mm, f"Opération annulée : {cot.motif_annulation[:70]}")
    c.setFillColor(SLATE)
    c.setFont("Helvetica", 6.5)
    c.drawCentredString(W / 2, 10 * mm,
                        f"Émis le {timezone.localtime(cot.cree_le):%d/%m/%Y à %H:%M} — conservez ce reçu.")
    c.showPage()
    c.save()
    return buf.getvalue()


def rapport(titre, entetes, lignes, resume) -> bytes:
    buf = BytesIO()
    paysage = len(entetes) > 6
    taille = landscape(A4) if paysage else A4
    doc = SimpleDocTemplate(buf, pagesize=taille, leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=30 * mm, bottomMargin=16 * mm, title=titre, author="FATONTINE CASH")
    W, H = taille

    def entete(c, d):
        c.saveState()
        c.setFillColor(NAVY)
        c.rect(0, H - 22 * mm, W, 22 * mm, fill=1, stroke=0)
        c.setFillColor(GOLD)
        c.rect(0, H - 23 * mm, W, 1 * mm, fill=1, stroke=0)
        _logo(c, 14 * mm, H - 18.5 * mm, r=7 * mm)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(32 * mm, H - 11 * mm, "FATONTINE CASH")
        c.setFillColor(colors.HexColor("#FBF0C9"))
        c.setFont("Helvetica", 8)
        c.drawString(32 * mm, H - 16 * mm, "La tontine, en toute simplicité.")
        c.setFillColor(colors.white)
        c.setFont("Helvetica", 7.5)
        c.drawRightString(W - 14 * mm, H - 11 * mm, f"Généré le {timezone.localtime():%d/%m/%Y %H:%M}")
        c.drawRightString(W - 14 * mm, H - 16 * mm, f"Page {d.page}")
        c.restoreState()

    st_titre = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=14, textColor=NAVY, spaceAfter=6)
    st_cell = ParagraphStyle("c", fontName="Helvetica", fontSize=7.5, leading=9)
    elements = [Paragraph(titre, st_titre), Spacer(1, 3 * mm)]

    if resume:
        t = Table([[Paragraph(f"<b>{k}</b>", st_cell), Paragraph(str(v), st_cell)] for k, v in resume],
                  colWidths=[70 * mm, 70 * mm], hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PEARL),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#E2E8F0")),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements += [t, Spacer(1, 6 * mm)]

    def fmt(v):
        return fcfa(v) if isinstance(v, int) and not isinstance(v, bool) and abs(v) >= 100 else str(v)

    if lignes:
        data = [[Paragraph(f"<b>{e}</b>", ParagraphStyle("h", parent=st_cell, textColor=colors.white))
                 for e in entetes]]
        data += [[Paragraph(fmt(v), st_cell) for v in l] for l in lignes]
        t = Table(data, repeatRows=1, colWidths=[(W - 28 * mm) / len(entetes)] * len(entetes))
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PEARL]),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(t)
    else:
        elements.append(Paragraph("Aucune opération sur cette sélection.",
                                  ParagraphStyle("v", parent=st_cell, alignment=TA_CENTER, textColor=SLATE)))

    doc.build(elements, onFirstPage=entete, onLaterPages=entete)
    return buf.getvalue()
