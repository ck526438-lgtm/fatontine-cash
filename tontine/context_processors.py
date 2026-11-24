from .models import Inscription, Parametres


def app_context(request):
    if not request.user.is_authenticated:
        return {}
    return {
        "nb_dossiers_attente": Inscription.objects.filter(statut=Inscription.Statut.EN_ATTENTE).count(),
        "parametres": Parametres.get(),
    }
