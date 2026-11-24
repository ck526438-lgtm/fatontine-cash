from urllib.parse import quote

from django.shortcuts import redirect

PUBLIC_PREFIXES = ("/connexion", "/mot-de-passe", "/static/", "/gestion-technique/", "/sante")


class LoginRequiredMiddleware:
    """Tout le site est privé : seule l'administratrice connectée y accède."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not request.user.is_authenticated and not request.path.startswith(PUBLIC_PREFIXES):
            return redirect(f"/connexion/?next={quote(request.path)}")
        response = self.get_response(request)
        if request.user.is_authenticated:
            response["Cache-Control"] = "no-store"
        return response
