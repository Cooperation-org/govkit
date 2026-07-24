"""Forms members use on themselves. Profile editing is self-serve: a signed-in
member edits their own public fields — no admin in the loop."""
from django import forms

from .models import User


class ProfileForm(forms.ModelForm):
    """A member edits their own public profile: the name and face other members
    see, plus a short bio in their own words. Email is identity (set by the auth
    provider) and is shown read-only elsewhere, so it is not editable here."""

    class Meta:
        model = User
        fields = ["display_name", "avatar_url", "bio"]
        labels = {
            "display_name": "Display name",
            "avatar_url": "Photo URL",
            "bio": "About you",
        }
        help_texts = {
            "avatar_url": "Link to a photo of you (e.g. your LinkedIn headshot URL).",
            "bio": "A sentence or two, in your own words.",
        }
        widgets = {
            "bio": forms.Textarea(attrs={"rows": 3}),
        }
