"""Object storage for things people upload about themselves.

Profile photos go to the SAME B2 bucket LinkedTrust puts claim media in
(golda 2026-07-27), so a person's face lives in one place whether it arrived
with a claim or from the profile page. B2 speaks S3, so this is the ordinary
S3 client pointed at B2's host.

Configuration is this app's own (GOVKIT_STORAGE_*), even when the values are
the same bucket and keys as LinkedTrust's: an app declares what it needs. All
empty is a supported state — the caller is told storage is off and the page
keeps working with a photo URL typed by hand.

Nothing here is public-facing except the returned URL. Keys are never logged.
"""

import logging
import mimetypes
import secrets

from django.conf import settings

logger = logging.getLogger(__name__)

# What a profile photo may be. No image decoding (no Pillow here), so this is
# an honest allow-list of what we will serve back, not a claim of validity.
ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_BYTES = 5 * 1024 * 1024


class StorageNotConfigured(Exception):
    """No bucket set. Not an error in itself — some installs have no storage."""


def configured() -> bool:
    return bool(
        settings.GOVKIT_STORAGE_BUCKET
        and settings.GOVKIT_STORAGE_KEY
        and settings.GOVKIT_STORAGE_SECRET
    )


def _endpoint() -> str:
    """The S3 host. B2's is derivable from the region, so a deploy that names a
    bucket and keys does not also have to know the URL by heart."""
    if settings.GOVKIT_STORAGE_ENDPOINT:
        return settings.GOVKIT_STORAGE_ENDPOINT
    return f"https://s3.{settings.GOVKIT_STORAGE_REGION}.backblazeb2.com"


def public_url(key: str) -> str:
    if settings.GOVKIT_STORAGE_PUBLIC_URL:
        return f"{settings.GOVKIT_STORAGE_PUBLIC_URL}/{key}"
    host = _endpoint().removeprefix("https://").removeprefix("http://")
    return f"https://{settings.GOVKIT_STORAGE_BUCKET}.{host}/{key}"


def _client():
    import boto3  # imported here so an install with no storage needs no boto3

    return boto3.client(
        "s3",
        endpoint_url=_endpoint(),
        aws_access_key_id=settings.GOVKIT_STORAGE_KEY,
        aws_secret_access_key=settings.GOVKIT_STORAGE_SECRET,
        region_name=settings.GOVKIT_STORAGE_REGION,
    )


def check_image(upload) -> str:
    """Return why this file is not acceptable, or "" if it is."""
    if upload.size > MAX_BYTES:
        return f"That image is {upload.size // 1024 // 1024}MB. The limit is 5MB."
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_TYPES:
        return "Use a JPEG, PNG, WebP or GIF."
    return ""


def store_image(upload, *, prefix: str) -> str:
    """Put an uploaded image in the bucket and return its public URL.

    The key is prefix + random, never the person's filename: an uploaded name
    is their words about their own file, not something to build a URL from.
    """
    if not configured():
        raise StorageNotConfigured()
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    suffix = ALLOWED_TYPES.get(content_type) or mimetypes.guess_extension(content_type) or ".jpg"
    key = f"{prefix.strip('/')}/{secrets.token_urlsafe(16)}{suffix}"
    upload.seek(0)
    _client().put_object(
        Bucket=settings.GOVKIT_STORAGE_BUCKET,
        Key=key,
        Body=upload.read(),
        ContentType=content_type,
    )
    return public_url(key)
