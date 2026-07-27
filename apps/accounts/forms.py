"""Forms members use on themselves. Profile editing is self-serve: a signed-in
member edits their own public fields — no admin in the loop."""

from django import forms

from apps.commons import storage

from .models import User


class ProfileForm(forms.ModelForm):
    """A member edits their own public profile: the name and face other members
    see, plus a short bio in their own words. Email is identity (set by the auth
    provider) and is shown read-only elsewhere, so it is not editable here.

    A photo can be uploaded or linked. Most people have a photo on their phone,
    not a URL for one, so the upload is the first control; the URL field stays
    for anyone who does have a link (and for installs with no storage).
    """

    photo = forms.FileField(
        required=False,
        label="Upload a photo",
        help_text="JPEG, PNG, WebP or GIF, up to 5MB.",
        widget=forms.ClearableFileInput(attrs={"accept": "image/*"}),
    )

    def clean_photo(self):
        upload = self.cleaned_data.get("photo")
        if not upload:
            return None
        problem = storage.check_image(upload)
        if problem:
            raise forms.ValidationError(problem)
        if not storage.configured():
            raise forms.ValidationError(
                "Uploads are not set up on this server yet. Paste a photo URL instead."
            )
        return upload

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
