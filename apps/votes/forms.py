"""Forms for the votes flow. Options are entered one-per-line and split in clean()."""

from django import forms


class CreateVoteForm(forms.Form):
    """Create a draft vote: a question and 2+ options (one per line)."""

    question = forms.CharField(max_length=500, widget=forms.TextInput)
    options = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="One option per line (at least two).",
    )

    def clean_options(self):
        raw = self.cleaned_data["options"]
        opts = [line.strip() for line in raw.splitlines() if line.strip()]
        if len(opts) < 2:
            raise forms.ValidationError("Enter at least two options, one per line.")
        if len(set(opts)) != len(opts):
            raise forms.ValidationError("Options must be distinct.")
        return opts
