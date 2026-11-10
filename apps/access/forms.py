"""Org-admin form for a tool connection. The admin secret is write-only: a typed value
replaces the stored one, a blank keeps it, and the stored value is never rendered."""

from django import forms

from .models import ToolConnection


class ToolConnectionForm(forms.ModelForm):
    admin_secret_input = forms.CharField(
        label="Admin password",
        required=False,
        widget=forms.PasswordInput(render_value=False),
    )

    class Meta:
        model = ToolConnection
        fields = [
            "kind",
            "display_name",
            "base_url",
            "database",
            "admin_login",
            "default_role",
            "enabled",
        ]
        labels = {
            "kind": "Tool",
            "display_name": "Name",
            "base_url": "URL",
            "database": "Odoo database",
            "admin_login": "Admin login",
            "default_role": "Taiga role to grant",
            "enabled": "On",
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        secret = self.cleaned_data.get("admin_secret_input")
        if secret:
            instance.admin_secret = secret
        if commit:
            instance.save()
        return instance
