from django import template
from django.utils.safestring import mark_safe

register = template.Library()

@register.filter
def break_before_paren(value):
    if not value:
        return value
    if "(" in value:
        before, after = value.split("(", 1)
        return mark_safe(f"{before.strip()}<br/>({after}")
    return value