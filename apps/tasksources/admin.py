from django import forms
from django.contrib import admin

from .models import TaskSourceConfig, TrackedTask


class TaskSourceConfigForm(forms.ModelForm):
    """Admin form that masks the encrypted api_token.

    M3: the real ``api_token`` field is decrypted on read, so rendering it as an editable
    form field would leak the cleartext tracker token to any staff user with admin access.
    Instead the token is write-only: it is never rendered, and a new value can only be SET
    (blank leaves the existing token untouched). A read-only status field shows set/unset.
    """

    api_token_input = forms.CharField(
        label="API token (write-only)",
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep the existing token. Entering a value replaces it. "
        "The stored token is encrypted at rest and never displayed here.",
    )

    class Meta:
        model = TaskSourceConfig
        # Deliberately exclude the encrypted api_token so its cleartext is never rendered.
        exclude = ("api_token",)

    def save(self, commit=True):
        instance = super().save(commit=False)
        new_token = self.cleaned_data.get("api_token_input")
        if new_token:
            instance.api_token = new_token
        if commit:
            instance.save()
            self.save_m2m()
        return instance


@admin.register(TaskSourceConfig)
class TaskSourceConfigAdmin(admin.ModelAdmin):
    form = TaskSourceConfigForm
    list_display = ("org", "adapter_type", "base_url", "project_selector", "updated_at")
    list_filter = ("adapter_type", "org")
    readonly_fields = ("api_token_status",)

    @admin.display(description="API token status")
    def api_token_status(self, obj):
        if obj is None or not (obj.api_token or "").strip():
            return "unset"
        return "set (hidden)"


@admin.register(TrackedTask)
class TrackedTaskAdmin(admin.ModelAdmin):
    list_display = (
        "external_id",
        "org",
        "source",
        "subject",
        "assignee",
        "claimed_value",
        "hours",
        "status",
        "fetched_at",
    )
    list_filter = ("org", "source", "status")
    search_fields = ("external_id", "subject")
    autocomplete_fields = ("assignee",)
