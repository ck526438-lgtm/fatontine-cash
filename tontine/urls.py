from django.urls import path

from . import auth_views as a
from . import views as v

urlpatterns = [
    path("connexion/", a.ConnexionView.as_view(), name="login"),
    path("deconnexion/", a.deconnexion, name="logout"),
    path("mot-de-passe/oublie/", a.ResetView.as_view(), name="password_reset"),
    path("mot-de-passe/envoye/", a.ResetDoneView.as_view(), name="password_reset_done"),
    path("mot-de-passe/nouveau/<uidb64>/<token>/", a.ResetConfirmView.as_view(), name="password_reset_confirm"),
    path("mot-de-passe/termine/", a.ResetCompleteView.as_view(), name="password_reset_complete"),
    path("parametres/mot-de-passe/", a.ChangementMotDePasseView.as_view(), name="password_change"),
    path("sante/", v.sante, name="sante"),

    path("", v.dashboard, name="dashboard"),
    path("inscriptions/", v.inscriptions, name="inscriptions"),
    path("inscriptions/nouvelle/", v.inscription_nouvelle, name="inscription_nouvelle"),
    path("inscriptions/<int:pk>/", v.inscription_detail, name="inscription_detail"),
    path("inscriptions/<int:pk>/valider/", v.inscription_valider, name="inscription_valider"),
    path("inscriptions/<int:pk>/refuser/", v.inscription_refuser, name="inscription_refuser"),
    path("inscriptions/<int:pk>/piece/<str:champ>/", v.piece_privee, name="piece_privee"),

    path("groupes/", v.groupes, name="groupes"),
    path("groupes/nouveau/", v.groupe_form, name="groupe_nouveau"),
    path("groupes/<int:pk>/", v.groupe_detail, name="groupe_detail"),
    path("groupes/<int:pk>/modifier/", v.groupe_form, name="groupe_modifier"),
    path("formules/", v.formules, name="formules"),

    path("membres/", v.membres, name="membres"),
    path("membres/<int:pk>/", v.membre_detail, name="membre_detail"),
    path("membres/<int:pk>/modifier/", v.membre_modifier, name="membre_modifier"),
    path("membres/<int:pk>/statut/", v.membre_statut, name="membre_statut"),

    path("cotisations/", v.cotisations, name="cotisations"),
    path("cotisations/<int:pk>/recu/", v.recu, name="recu"),
    path("cotisations/<int:pk>/recu.pdf", v.recu_pdf, name="recu_pdf"),
    path("cotisations/<int:pk>/annuler/", v.cotisation_annuler, name="cotisation_annuler"),

    path("beneficiaires/", v.beneficiaires, name="beneficiaires"),
    path("beneficiaires/<int:pk>/", v.tour_detail, name="tour_detail"),
    path("transactions/", v.transactions, name="transactions"),
    path("notifications/", v.notifications, name="notifications"),
    path("rapports/", v.rapports, name="rapports"),
    path("rapports/<str:rapport>.<str:fmt>", v.export, name="export"),
    path("parametres/", v.parametres, name="parametres"),
    path("journal/", v.journal, name="journal"),
]
