import re

from django import forms
from django.core.validators import MinValueValidator
from django.db import models

from .validation import validate_positive_integer


class StrictPositiveIntegerFormField(forms.IntegerField):
    def to_python(self, value):
        if value not in self.empty_values and type(value) is not int:
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value.strip()):
                raise forms.ValidationError("必须填写正整数，不能是小数、布尔值或表达式。", code="invalid")
        return super().to_python(value)


class StrictPositiveIntegerField(models.PositiveIntegerField):
    """Keep model validation from silently coercing booleans or fractional values."""

    default_validators = [MinValueValidator(1)]

    def to_python(self, value):
        validate_positive_integer(value)
        return value

    def formfield(self, **kwargs):
        return super().formfield(form_class=StrictPositiveIntegerFormField, **{"min_value": 1, **kwargs})
