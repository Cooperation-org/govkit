"""One way to ask a person for a picture: upload it, or link to one.

Wherever GovKit shows a picture — a member's face, a team's logo, the image on
a shared link, the photo on an invite — the person is asked the same way and
the upload is the first control. Most people have the picture on their laptop,
not a URL for it; asking for a URL is asking them to go and host a file first.

Three pieces, so no screen has to reinvent any of it:

    upload_field()   the file input, already carrying the size and type rules
    clean_upload()   what a form's clean_<field> does with what came back
    store(...)       put it in the bucket, or tell the person it did not work

The URL field always stays. Someone who already has a link should not have to
download their own picture to upload it again, and an install with no bucket
configured keeps working with URLs alone — see storage.configured().
"""

import logging

from django import forms

from . import storage

logger = logging.getLogger(__name__)

HELP = "JPEG, PNG, WebP or GIF, up to 5MB."


def upload_field(label="Upload a picture"):
    """The file input for a picture. Never required — the URL field is the
    other way to answer the same question."""
    return forms.FileField(
        required=False,
        label=label,
        help_text=HELP,
        widget=forms.ClearableFileInput(attrs={"accept": "image/*"}),
    )


def clean_upload(upload, *, url_label="URL"):
    """Validate what the file input returned. Call from a form's clean_<field>.

    Returns the upload (or None). Raises ValidationError with words a person can
    act on: what is wrong with the file, or that this server has no bucket and
    the URL field is the way through.
    """
    if not upload:
        return None
    problem = storage.check_image(upload)
    if problem:
        raise forms.ValidationError(problem)
    if not storage.configured():
        raise forms.ValidationError(
            f"Uploads are not set up on this server yet. Paste a {url_label} instead."
        )
    return upload


def store(upload, *, prefix, what):
    """Put the picture in the bucket and return its URL, or "" if it failed.

    An upload that fails must not take the rest of someone's edits down with
    it, so this reports rather than raises and the caller keeps the form up
    with everything they typed still in it. `what` names the picture in the
    message a person reads ("Your logo did not upload").
    """
    try:
        return storage.store_image(upload, prefix=prefix)
    except Exception:
        logger.exception("%s upload failed (prefix=%s)", what, prefix)
        return ""


def failed_message(what):
    """What a person is told when the bucket would not take their picture."""
    return f"{what} did not upload. Nothing else was changed — try again."
