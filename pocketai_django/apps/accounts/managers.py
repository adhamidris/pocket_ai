from __future__ import annotations

from typing import Any

from django.contrib.auth.base_user import BaseUserManager
from django.utils import timezone


class UserManager(BaseUserManager):
    """Custom manager using email as the unique username field."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra_fields: Any):
        if not email:
            raise ValueError("The email address must be provided.")
        email = self.normalize_email(email)
        now = timezone.now()
        extra_fields.setdefault("created_at", now)
        extra_fields.setdefault("updated_at", now)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra_fields: Any):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        extra_fields.setdefault("status", "active")
        return self._create_user(email=email, password=password, **extra_fields)

    def create_superuser(self, email: str, password: str | None, **extra_fields: Any):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("status", "active")

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self._create_user(email=email, password=password, **extra_fields)

