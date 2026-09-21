"""Template filters and tags. Presentation only; no queries, no business rules."""

from __future__ import annotations

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core import formatting

register = template.Library()


@register.simple_tag
def icon(name: str, css: str = "icon", label: str = "") -> str:
    """
    Reference one symbol from the shared sprite.

    Decorative by default (``aria-hidden``). Pass ``label`` when the icon is
    the only content of a control and therefore carries the meaning.
    """
    if label:
        return format_html(
            '<svg class="{}" role="img" aria-label="{}">'
            '<use href="/static/icons/sprite.svg#i-{}"></use></svg>',
            css,
            label,
            name,
        )
    return format_html(
        '<svg class="{}" aria-hidden="true">'
        '<use href="/static/icons/sprite.svg#i-{}"></use></svg>',
        css,
        name,
    )


@register.filter
def money(value, currency: str = "INR") -> str:
    return formatting.money(value, currency)


@register.filter
def money_exact(value) -> str:
    return formatting.money_exact(value)


@register.filter
def compact(value) -> str:
    return formatting.compact(value)


@register.filter
def percent(value) -> str:
    return formatting.percent(value)


@register.filter
def percent_signed(value) -> str:
    return formatting.percent(value, signed=True)


@register.filter
def ago(value) -> str:
    return formatting.relative_time(value)


@register.filter
def delta_class(value) -> str:
    """``gain`` / ``loss`` / ``muted`` for a numeric change."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "muted"
    if number > 0:
        return "gain"
    if number < 0:
        return "loss"
    return "muted"


@register.filter
def initials(value) -> str:
    text = str(value or "").strip()
    if not text:
        return "?"
    parts = text.split()
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


@register.simple_tag(takes_context=True)
def query_replace(context, **kwargs) -> str:
    """
    Rebuild the current query string with some keys replaced.

    Pagination and sort controls use this so they carry the active filters
    forward instead of resetting them. An empty value removes the key.
    """
    params = context["request"].GET.copy()
    for key, value in kwargs.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return mark_safe(f"?{encoded}" if encoded else "?")


@register.simple_tag(takes_context=True)
def query_without(context, *keys) -> str:
    """The current query string minus these keys. Drives the filter chips."""
    params = context["request"].GET.copy()
    for key in (*keys, "page"):
        params.pop(key, None)
    encoded = params.urlencode()
    return mark_safe(f"?{encoded}" if encoded else "?")


@register.filter
def field_type(field) -> str:
    """Widget class name, so templates can branch on input kind."""
    return field.field.widget.__class__.__name__
