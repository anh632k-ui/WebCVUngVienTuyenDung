from __future__ import annotations

from pydantic import EmailStr, TypeAdapter, ValidationError

_PROFILE_EMAIL_ADAPTER = TypeAdapter(EmailStr)


def normalize_untrusted_profile_email(value: object) -> str | None:
    """Use the API's EmailStr rules for extracted or legacy profile emails."""
    if not isinstance(value, str):
        return None
    try:
        return str(_PROFILE_EMAIL_ADAPTER.validate_python(value))
    except ValidationError:
        return None
