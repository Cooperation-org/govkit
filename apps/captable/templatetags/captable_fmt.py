"""Money as people read it on a cap table: grouped digits, cents only when there are any."""

from decimal import Decimal

from django import template

register = template.Library()

SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


@register.filter
def money(value, currency="USD"):
    if value is None or value == "":
        return ""
    d = Decimal(value)
    text = f"{d:,.0f}" if d == d.to_integral_value() else f"{d:,.2f}"
    sym = SYMBOLS.get(currency)
    return f"{sym}{text}" if sym else f"{text} {currency}"


@register.filter
def plain(value):
    """A Decimal for an <input type=number>: no trailing zeros, never exponent form."""
    if value is None:
        return ""
    d = Decimal(value).normalize()
    return format(d, "f")


@register.filter
def as_pct(fraction):
    """An OCF fraction (0.2) as the percent people type (20)."""
    if fraction is None or fraction == "":
        return ""
    d = (Decimal(fraction) * 100).normalize()
    return format(d, "f")


@register.filter
def shares(value):
    """A share count with grouped digits."""
    if value is None or value == "":
        return ""
    return f"{int(value):,}"


@register.filter
def get_item(mapping, key):
    return mapping.get(key) if mapping else None
