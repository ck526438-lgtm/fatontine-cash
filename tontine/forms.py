from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.utils import timezone

from .models import Cotisation, Formule, Groupe, Inscription, Membre, Mouvement, Parametres, Tour

TYPES_AUTORISES = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
EXTENSIONS_AUTORISEES = {"jpg", "jpeg", "png", "webp", "pdf"}
SIGNATURES = (b"\xff\xd8\xff", b"\x89PNG", b"RIFF", b"%PDF")


def valider_fichier(f):
    if not f:
        return f
    if f.size > settings.UPLOAD_MAX_BYTES:
        raise forms.ValidationError("Fichier trop lourd (5 Mo maximum).")
    ext = f.name.rsplit(".", 1)[-1].lower() if "." in f.name else ""
    if ext not in EXTENSIONS_AUTORISEES:
        raise forms.ValidationError("Formats acceptés : JPG, PNG, WEBP ou PDF.")
    entete = f.read(8)
    f.seek(0)
    if not entete.startswith(SIGNATURES):
        raise forms.ValidationError("Le contenu du fichier ne correspond pas à une image ou un PDF.")
    return f


class GlassMixin:
    """Applique les classes du design glass à tous les champs."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            w = field.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs.setdefault("class", "g-check")
            elif isinstance(w, forms.FileInput):
                w.attrs.setdefault("class", "g-file")
                w.attrs.setdefault("accept", ".jpg,.jpeg,.png,.webp,.pdf")
            else:
                w.attrs.setdefault("class", "g-input")
            if isinstance(field, forms.ModelChoiceField) and field.empty_label:
                field.empty_label = "Choisir…"
            elif isinstance(field, forms.TypedChoiceField) and field.choices and field.choices[0][0] == "":
                field.choices = [("", "Choisir…")] + list(field.choices)[1:]


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%d", **kwargs)


class ConnexionForm(GlassMixin, AuthenticationForm):
    username = forms.CharField(label="Identifiant", widget=forms.TextInput(attrs={"autocomplete": "username", "autofocus": True}))
    password = forms.CharField(label="Mot de passe", strip=False,
                               widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))


class InscriptionForm(GlassMixin, forms.ModelForm):
    class Meta:
        model = Inscription
        fields = ["nom", "prenoms", "sexe", "date_naissance", "telephone", "adresse",
                  "type_piece", "numero_piece", "fichier_piece", "photo",
                  "temoin_nom", "temoin_telephone", "formule_souhaitee", "consentement"]
        widgets = {
            "date_naissance": DateInput(),
            "nom": forms.TextInput(attrs={"placeholder": "Ex : KOUASSI"}),
            "prenoms": forms.TextInput(attrs={"placeholder": "Ex : Aya Marie"}),
            "telephone": forms.TextInput(attrs={"placeholder": "07 00 00 00 00", "inputmode": "tel"}),
            "adresse": forms.TextInput(attrs={"placeholder": "Ex : Abidjan, Cocody"}),
            "numero_piece": forms.TextInput(attrs={"placeholder": "Ex : CI004589210"}),
            "temoin_nom": forms.TextInput(attrs={"placeholder": "Ex : KONÉ Ibrahim"}),
            "temoin_telephone": forms.TextInput(attrs={"placeholder": "05 00 00 00 00", "inputmode": "tel"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["formule_souhaitee"].queryset = Formule.objects.filter(active=True)
        self.fields["formule_souhaitee"].empty_label = None
        self.fields["consentement"].required = True
        if not self.instance.pk:
            self.fields["fichier_piece"].required = True
            self.fields["photo"].required = True

    def clean_fichier_piece(self):
        return valider_fichier(self.cleaned_data.get("fichier_piece"))

    def clean_photo(self):
        return valider_fichier(self.cleaned_data.get("photo"))

    def clean_date_naissance(self):
        d = self.cleaned_data["date_naissance"]
        today = timezone.localdate()
        age = today.year - d.year - ((today.month, today.day) < (d.month, d.day))
        if age < 18:
            raise forms.ValidationError("Le membre doit être majeur (18 ans ou plus).")
        if age > 110:
            raise forms.ValidationError("Date de naissance invalide.")
        return d

    def clean(self):
        data = super().clean()
        tel, ttel = data.get("telephone", ""), data.get("temoin_telephone", "")
        chiffres = lambda s: "".join(c for c in s if c.isdigit())[-10:]
        if tel and ttel and chiffres(tel) == chiffres(ttel):
            self.add_error("temoin_telephone", "Le témoin doit avoir un numéro différent de celui du membre.")
        return data


class ValidationInscriptionForm(GlassMixin, forms.Form):
    groupe = forms.ModelChoiceField(queryset=Groupe.objects.none(), label="Groupe d'affectation")
    observation = forms.CharField(label="Observation (facultatif)", required=False,
                                  widget=forms.Textarea(attrs={"rows": 2}))
    pieces_verifiees = forms.BooleanField(label="J'ai vérifié la pièce d'identité, la photo et joint le témoin.",
                                          required=True)

    def __init__(self, inscription, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ids = [g.pk for g in Groupe.objects.filter(statut__in=[Groupe.Statut.OUVERT, Groupe.Statut.EN_COURS])
               if not g.est_complet]
        qs = Groupe.objects.filter(pk__in=ids).select_related("formule")
        self.fields["groupe"].queryset = qs
        self.fields["groupe"].label_from_instance = (
            lambda g: f"{g.nom} — {g.formule} — {g.places_libres()} place(s)")
        meme = qs.filter(formule=inscription.formule_souhaitee).first()
        if meme:
            self.fields["groupe"].initial = meme.pk


class RefusInscriptionForm(GlassMixin, forms.Form):
    motif = forms.CharField(label="Motif du refus", widget=forms.Textarea(attrs={"rows": 3}))


class GroupeForm(GlassMixin, forms.ModelForm):
    class Meta:
        model = Groupe
        fields = ["nom", "formule", "date_demarrage", "capacite", "jours_par_tour", "notes"]
        widgets = {"date_demarrage": DateInput(), "notes": forms.Textarea(attrs={"rows": 2}),
                   "nom": forms.TextInput(attrs={"placeholder": "Ex : Groupe Cocody 550"})}
        labels = {"capacite": "Participants (max.)", "jours_par_tour": "Jours entre deux distributions"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        p = Parametres.get()
        self.fields["formule"].queryset = Formule.objects.filter(active=True)
        if not self.instance.pk:
            self.fields["capacite"].initial = p.taille_groupe
            self.fields["jours_par_tour"].initial = p.jours_par_tour
            self.fields["date_demarrage"].initial = timezone.localdate()
        else:
            if self.instance.cotisations.exists():
                for f in ("formule", "capacite", "jours_par_tour", "date_demarrage"):
                    self.fields[f].disabled = True
                    self.fields[f].help_text = "Verrouillé : des cotisations existent déjà."

    def clean_capacite(self):
        c = self.cleaned_data["capacite"]
        if self.instance.pk and c < self.instance.places_occupees():
            raise forms.ValidationError("La capacité ne peut pas être inférieure au nombre de membres.")
        return c


class MembreForm(GlassMixin, forms.ModelForm):
    class Meta:
        model = Membre
        fields = ["nom", "prenoms", "telephone", "adresse", "temoin_nom", "temoin_telephone"]


class StatutMembreForm(GlassMixin, forms.Form):
    statut = forms.ChoiceField(choices=Membre.Statut.choices)
    motif = forms.CharField(label="Motif (obligatoire)", widget=forms.Textarea(attrs={"rows": 2}))


class CotisationForm(GlassMixin, forms.ModelForm):
    class Meta:
        model = Cotisation
        fields = ["membre", "nb_jours", "date_paiement", "mode", "id_wave", "verifie", "note"]
        widgets = {"date_paiement": DateInput(),
                   "id_wave": forms.TextInput(attrs={"placeholder": "Ex : T_CI89451203",
                                                     "style": "text-transform:uppercase"}),
                   "note": forms.TextInput(attrs={"placeholder": "Facultatif"})}
        labels = {"verifie": "J'ai vérifié la réception réelle du paiement (relevé Wave ou espèces en main)."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["membre"].queryset = (Membre.objects.filter(statut=Membre.Statut.ACTIF)
                                          .select_related("groupe__formule").order_by("groupe__nom", "nom"))
        self.fields["membre"].label_from_instance = (
            lambda m: f"{m.nom_complet} — {m.groupe.nom} ({m.groupe.formule.cotisation_journaliere} F/j)")
        self.fields["date_paiement"].initial = timezone.localdate()
        self.fields["verifie"].required = True
        self.fields["id_wave"].required = False
        self.fields["mode"].choices = Cotisation.Mode.choices
        self.fields["mode"].initial = Cotisation.Mode.WAVE

    def clean_date_paiement(self):
        d = self.cleaned_data["date_paiement"]
        if d > timezone.localdate():
            raise forms.ValidationError("La date ne peut pas être dans le futur.")
        return d

    def clean(self):
        data = super().clean()
        mode, ref = data.get("mode"), (data.get("id_wave") or "").strip().upper()
        if mode == Cotisation.Mode.WAVE:
            if not ref:
                self.add_error("id_wave", "L'ID de transaction Wave est obligatoire.")
            elif Cotisation.objects.filter(id_wave=ref).exists():
                self.add_error("id_wave", "Cet ID Wave a déjà été utilisé pour une autre opération.")
            data["id_wave"] = ref
        else:
            data["id_wave"] = None
        return data


class AnnulationForm(GlassMixin, forms.Form):
    motif = forms.CharField(label="Motif de l'annulation", widget=forms.Textarea(attrs={"rows": 2}))


class ChangementRangForm(GlassMixin, forms.Form):
    nouveau_rang = forms.IntegerField(label="Nouveau rang", min_value=1)
    justification = forms.CharField(label="Justification obligatoire", widget=forms.Textarea(
        attrs={"rows": 3, "placeholder": "Ex : accord mutuel entre les deux membres, validé par Fatima"}))

    def __init__(self, tour: Tour, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["nouveau_rang"].max_value = tour.groupe.capacite
        self.fields["nouveau_rang"].widget.attrs["max"] = tour.groupe.capacite


class DistributionForm(GlassMixin, forms.Form):
    mode = forms.ChoiceField(choices=Cotisation.Mode.choices, label="Mode de remise")
    id_wave = forms.CharField(label="ID transaction Wave (si Wave)", required=False)
    note = forms.CharField(label="Note", required=False)
    confirmee = forms.BooleanField(label="Le membre a confirmé la réception", required=False)


class MouvementForm(GlassMixin, forms.ModelForm):
    """Dépense autorisée ou correction manuelle."""

    class Meta:
        model = Mouvement
        fields = ["type", "libelle", "montant", "date", "groupe", "operation_liee"]
        widgets = {"date": DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = timezone.localdate()


class FormuleForm(GlassMixin, forms.ModelForm):
    class Meta:
        model = Formule
        fields = ["code", "cotisation_journaliere", "reception_annoncee", "active", "ordre"]


class ParametresForm(GlassMixin, forms.ModelForm):
    class Meta:
        model = Parametres
        fields = ["taille_groupe", "jours_par_tour", "conservation_pieces_mois", "telephone_contact"]
