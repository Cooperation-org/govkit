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

# What a picture may be. The allow-list is what we will serve back; Pillow,
# when it is installed, is what actually reads the file (see _shrink) and so is
# also what catches a .jpg that is not one.
ALLOWED_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
# Generous on the way in, small on the way out (golda 2026-07-28). A picture
# off a phone is 10-20MB and nobody should have to shrink it first, so we take
# it and shrink it ourselves before it goes in the bucket. What a visitor
# downloads is PAGE_WIDE, not this.
MAX_BYTES = 25 * 1024 * 1024

# The longest edge we serve. 1600 covers a full-width picture on a retina
# laptop; a thumbnail in a grid never needs more than 600.
PAGE_WIDE = 1600
THUMB_WIDE = 600


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
        return (
            f"That image is {upload.size // 1024 // 1024}MB. "
            f"The limit is {MAX_BYTES // 1024 // 1024}MB."
        )
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_TYPES:
        return "Use a JPEG, PNG, WebP or GIF."
    return ""


def _shrink(data: bytes, content_type: str, longest: int):
    """Return (bytes, content_type) scaled to fit `longest`, or None to send as is.

    None covers every case where shrinking is the wrong move: no Pillow on this
    install, an animated GIF (resizing would flatten it to one frame), a picture
    already smaller than the target, or anything Pillow cannot read. The caller
    then stores the original, which is what happened before this existed.
    """
    try:
        import io

        from PIL import Image
    except ImportError:
        return None
    if content_type == "image/gif":
        return None
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        logger.warning("could not read an uploaded image; storing it as it arrived")
        return None
    if max(image.size) <= longest:
        return None
    image.thumbnail((longest, longest), Image.LANCZOS)
    out = io.BytesIO()
    if content_type == "image/png":
        image.save(out, format="PNG", optimize=True)
    elif content_type == "image/webp":
        image.save(out, format="WEBP", quality=85, method=4)
    else:
        image = image.convert("RGB")
        image.save(out, format="JPEG", quality=85, optimize=True, progressive=True)
        content_type = "image/jpeg"
    return out.getvalue(), content_type


def _put(data: bytes, content_type: str, *, prefix: str) -> str:
    """Write one object and return its public URL.

    The key is prefix + random, never the person's filename: an uploaded name
    is their words about their own file, not something to build a URL from.
    """
    suffix = ALLOWED_TYPES.get(content_type) or mimetypes.guess_extension(content_type) or ".jpg"
    key = f"{prefix.strip('/')}/{secrets.token_urlsafe(16)}{suffix}"
    _client().put_object(
        Bucket=settings.GOVKIT_STORAGE_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    return public_url(key)


def store_image(upload, *, prefix: str) -> str:
    """Put an uploaded image in the bucket and return its public URL.

    Shrunk to PAGE_WIDE on the way in. A team may upload the picture straight
    off their phone; every visitor to their page then downloads whatever we
    kept, so what we keep is the size a page needs.
    """
    if not configured():
        raise StorageNotConfigured()
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    upload.seek(0)
    data = upload.read()
    shrunk = _shrink(data, content_type, PAGE_WIDE)
    if shrunk:
        data, content_type = shrunk
    return _put(data, content_type, prefix=prefix)


def store_image_pair(upload, *, prefix: str):
    """Store one picture twice: page-sized, and a thumbnail for a grid.

    Returns (url, thumb_url). thumb_url is "" when the picture was already
    small enough to be its own thumbnail, and the caller falls back to url.
    """
    if not configured():
        raise StorageNotConfigured()
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    upload.seek(0)
    data = upload.read()

    page = _shrink(data, content_type, PAGE_WIDE)
    page_data, page_type = page if page else (data, content_type)
    url = _put(page_data, page_type, prefix=prefix)

    thumb = _shrink(data, content_type, THUMB_WIDE)
    if not thumb:
        return url, ""
    return url, _put(thumb[0], thumb[1], prefix=f"{prefix.strip('/')}/thumb")
