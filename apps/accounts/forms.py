"""Forms members use on themselves. Profile editing is self-serve: a signed-in
member edits their own public fields — no admin in the loop."""

from django import forms

from apps.commons import pictures

from .models import User


class ProfileForm(forms.ModelForm):
    """A member edits their own public profile: the name and face other members
    see, plus a short bio in their own words. Email is identity (set by the auth
    provider) and is shown read-only elsewhere, so it is not editable here.

    A photo can be uploaded or linked. Most people have a photo on their phone,
    not a URL for one, so the upload is the first control; the URL field stays
    for anyone who does have a link (and for installs with no storage).

    The booking link is here because a mentor gave one when they joined, on a
    signed claim they cannot edit. Changing calendar tools is ordinary; being
    stuck with a dead booking link is not.
    """

    photo = pictures.upload_field("Upload a photo")
    # A pasted "calendly.com/ada" is what people actually have to hand.
    calendar_url = forms.URLField(
        max_length=1000,
        required=False,
        assume_scheme="https",
        label="Booking link",
        help_text="Where people book time with you. Teams see it on the Mentors page.",
    )

    def clean_photo(self):
        return pictures.clean_upload(self.cleaned_data.get("photo"), url_label="photo URL")

    class Meta:
        model = User
        fields = ["display_name", "avatar_url", "bio", "calendar_url"]
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
