"""
Org-admin connect form for a task source (the HTML counterpart of the admin form).

Same write-only token discipline as admin.TaskSourceConfigForm: the encrypted
``api_token`` is never rendered — a masked input SETS a new token, blank keeps the stored
one. ``done_statuses`` (a JSON list on the model) is edited as comma-separated text so
admins never touch JSON.
"""

from django import forms

from .models import TaskSourceConfig, default_done_statuses


class TaskSourceConnectForm(forms.ModelForm):
    api_token_input = forms.CharField(
        label="API token",
        required=False,
        widget=forms.PasswordInput(render_value=False),
    )
    done_statuses_input = forms.CharField(
        label="Done statuses",
        required=False,
    )

    class Meta:
        model = TaskSourceConfig
        # org comes from the URL (the view sets it); api_token is write-only via
        # api_token_input; done_statuses is edited via done_statuses_input.
        fields = [
            "adapter_type",
            "base_url",
            "project_selector",
            "value_tag_pattern",
            "hours_field",
            "cash_field",
        ]
        labels = {
            "adapter_type": "Tracker",
            "base_url": "Base URL",
            "project_selector": "Project",
            "value_tag_pattern": "Value tag pattern",
            "hours_field": "Hours field",
            "cash_field": "Cash field",
        }
        # Model help_texts are written for the Django admin; the org-facing page is
        # label-only (any helper wording there is Golda's to write).
        help_texts = {
            "adapter_type": "",
            "base_url": "",
            "project_selector": "",
            "value_tag_pattern": "",
            "hours_field": "",
            "cash_field": "",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        statuses = None
        if self.instance and self.instance.pk:
            statuses = self.instance.done_statuses
        self.fields["done_statuses_input"].initial = ", ".join(
            statuses if statuses is not None else default_done_statuses()
        )

    def save(self, commit=True):
        instance = super().save(commit=False)
        new_token = self.cleaned_data.get("api_token_input")
        if new_token:
            instance.api_token = new_token
        raw = self.cleaned_data.get("done_statuses_input", "") or ""
        statuses = [s.strip() for s in raw.split(",") if s.strip()]
        # Empty input falls back to the model default — a source with no done statuses
        # would silently match nothing.
        instance.done_statuses = statuses or default_done_statuses()
        if commit:
            instance.save()
        return instance
