from django import template

from ..services import fcfa as _fcfa

register = template.Library()


@register.filter
def fcfa(value):
    return _fcfa(value)


@register.filter
def milliers(value):
    return f"{int(value or 0):,}".replace(",", " ")


@register.simple_tag(takes_context=True)
def actif(context, *noms):
    match = getattr(context["request"], "resolver_match", None)
    return "is-active" if match and match.url_name in noms else ""


@register.filter
def absolu(value):
    try:
        return abs(value)
    except TypeError:
        return value
