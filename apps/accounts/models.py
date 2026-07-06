"""
Custom, email-based user model.

Email is the username field. Passwords are optional (nullable) so OAuth/OIDC users
who never set one are first-class. External-identity fields (auth_provider /
auth_provider_id) let the auth agent map an OIDC subject to a local user without
overloading email.
"""

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    """Manager keyed on email instead of username."""

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            # OAuth/OIDC users may never have a usable password.
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    display_name = models.CharField(max_length=255, blank=True)
    avatar_url = models.URLField(blank=True)

    # External identity (OAuth/OIDC). Explicit, never inferred.
    auth_provider = models.CharField(
        max_length=50,
        blank=True,
        help_text="e.g. 'linkedtrust', 'google'. Blank for dev/password users.",
    )
    auth_provider_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="The subject/id at the auth provider (OIDC 'sub').",
    )

    is_staff = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        constraints = [
            models.UniqueConstraint(
                fields=["auth_provider", "auth_provider_id"],
                name="uniq_auth_provider_identity",
                condition=models.Q(auth_provider_id__gt=""),
            ),
        ]

    def __str__(self):
        return self.email

    def get_full_name(self):
        return self.display_name or self.email

    def get_short_name(self):
        return self.display_name or self.email.split("@")[0]
