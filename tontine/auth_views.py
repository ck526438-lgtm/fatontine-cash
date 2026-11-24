"""Connexion avec protection contre les tentatives répétées (§5.1)."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .forms import ConnexionForm
from .services import client_ip, journaliser


def _cle(request, username):
    return f"login-fail:{client_ip(request)}:{(username or '').lower()}"


def _cle_ip(request):
    return f"login-fail-ip:{client_ip(request)}"


class ConnexionView(auth_views.LoginView):
    template_name = "registration/login.html"
    authentication_form = ConnexionForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        username = request.POST.get("username", "")
        maxi = settings.LOGIN_MAX_ATTEMPTS
        if cache.get(_cle(request, username), 0) >= maxi or cache.get(_cle_ip(request), 0) >= maxi * 3:
            journaliser(request, "connexion.bloquee", username)
            form = self.get_form()
            minutes = settings.LOGIN_LOCKOUT_SECONDS // 60
            return render(request, self.template_name, {
                "form": form, "verrouille": True,
                "message_blocage": f"Trop de tentatives. Réessayez dans {minutes} minutes.",
            }, status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        username = self.request.POST.get("username", "")
        for cle in (_cle(self.request, username), _cle_ip(self.request)):
            cache.add(cle, 0, settings.LOGIN_LOCKOUT_SECONDS)
            try:
                cache.incr(cle)
            except ValueError:
                cache.set(cle, 1, settings.LOGIN_LOCKOUT_SECONDS)
        restantes = max(settings.LOGIN_MAX_ATTEMPTS - cache.get(_cle(self.request, username), 0), 0)
        journaliser(self.request, "connexion.echec", username)
        return render(self.request, self.template_name, {"form": form, "tentatives_restantes": restantes})

    def form_valid(self, form):
        cache.delete(_cle(self.request, form.get_user().get_username()))
        response = super().form_valid(form)
        journaliser(self.request, "connexion.reussie", form.get_user().get_username())
        messages.success(self.request, f"Bienvenue, {form.get_user().first_name or form.get_user().username} !")
        return response


@require_POST
def deconnexion(request):
    from django.contrib.auth import logout
    journaliser(request, "deconnexion", request.user.get_username() if request.user.is_authenticated else "")
    logout(request)
    messages.info(request, "Vous êtes déconnectée en toute sécurité.")
    return redirect("login")


class ResetView(auth_views.PasswordResetView):
    template_name = "registration/reset_form.html"
    email_template_name = "registration/reset_email.txt"
    subject_template_name = "registration/reset_subject.txt"
    success_url = "/mot-de-passe/envoye/"


class ResetDoneView(auth_views.PasswordResetDoneView):
    template_name = "registration/reset_done.html"


class ResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = "registration/reset_confirm.html"
    success_url = "/mot-de-passe/termine/"


class ResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = "registration/reset_complete.html"


class ChangementMotDePasseView(auth_views.PasswordChangeView):
    template_name = "registration/password_change.html"
    success_url = "/parametres/"

    def form_valid(self, form):
        r = super().form_valid(form)
        journaliser(self.request, "mot_de_passe.modifie", self.request.user.get_username())
        messages.success(self.request, "Mot de passe modifié.")
        return r
