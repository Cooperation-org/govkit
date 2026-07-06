"""
EncryptedTextField — symmetric encryption at rest for secrets like API tokens.

Uses Fernet (AES-128-CBC + HMAC) keyed on settings.GOVKIT_SECRET_KEY. Ciphertext is
stored in the DB; the Python attribute is always plaintext. If GOVKIT_SECRET_KEY is unset,
writing a non-empty value raises — we never silently persist a secret in plaintext.

Generate a key with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

logger = logging.getLogger(__name__)


def _fernet():
    key = getattr(settings, "GOVKIT_SECRET_KEY", "")
    if not key:
        raise ImproperlyConfigured(
            "GOVKIT_SECRET_KEY is required to store encrypted fields. "
            "Generate one with cryptography.fernet.Fernet.generate_key()."
        )
    if isinstance(key, str):
        key = key.encode()
    return Fernet(key)


class EncryptedTextField(models.TextField):
    """Transparently encrypts on write, decrypts on read."""

    def get_prep_value(self, value):
        if value is None or value == "":
            return value
        token = _fernet().encrypt(str(value).encode()).decode()
        return token

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            # L9: the stored value cannot be decrypted (key rotated, wrong key, or a legacy
            # plaintext value). NEVER return the raw stored bytes — that would hand the
            # ciphertext to callers (e.g. the tracker adapter) as if it were the real token.
            # Fail SAFE: log loudly and return None so reads (incl. admin list/detail views)
            # don't crash, and downstream treats the secret as absent rather than sending
            # garbage. Re-save the field with a valid key to restore it.
            logger.error(
                "EncryptedTextField %s.%s: stored value could not be decrypted "
                "(key rotation or corruption). Returning None; re-save to restore.",
                (
                    getattr(self, "model", type(self)).__name__
                    if hasattr(self, "model")
                    else "<unknown>"
                ),
                self.attname,
            )
            return None
