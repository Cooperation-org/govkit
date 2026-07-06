"""Forms for the steward drop flow. The adjust form mirrors DropLine.clean()."""

from decimal import Decimal

from django import forms


class AdjustLineForm(forms.Form):
    """Per-line adjustment. A non-zero adjustment requires a reason (audit trail)."""

    adjustment = forms.DecimalField(max_digits=16, decimal_places=2, initial=Decimal("0"))
    adjustment_reason = forms.CharField(max_length=500, required=False, widget=forms.TextInput)

    def clean(self):
        cleaned = super().clean()
        adjustment = cleaned.get("adjustment")
        reason = (cleaned.get("adjustment_reason") or "").strip()
        if adjustment and adjustment != Decimal("0") and not reason:
            self.add_error(
                "adjustment_reason", "A reason is required when an adjustment is applied."
            )
        return cleaned
