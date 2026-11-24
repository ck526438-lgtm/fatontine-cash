"""Admin technique (dépannage). L'usage quotidien passe par l'interface Liquid Glass."""
from django.contrib import admin

from . import models


class LectureSeule(admin.ModelAdmin):
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(models.JournalAudit)
class JournalAdmin(LectureSeule):
    list_display = ("le", "utilisateur", "action", "objet", "ip")
    list_filter = ("action",)
    search_fields = ("objet", "details")

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False


@admin.register(models.Cotisation)
class CotisationAdmin(LectureSeule):
    list_display = ("numero_recu", "reference", "membre", "montant", "mode", "id_wave", "date_paiement", "annulee")
    search_fields = ("numero_recu", "reference", "id_wave", "membre__nom")
    readonly_fields = [f.name for f in models.Cotisation._meta.fields]


for m in (models.Formule, models.Groupe, models.Inscription, models.Membre, models.Tour, models.Mouvement,
          models.ChangementOrdre, models.Parametres):
    admin.site.register(m, LectureSeule)
