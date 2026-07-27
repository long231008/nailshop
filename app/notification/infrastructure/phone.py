"""Turn the numbers customers type into the format carriers accept."""

from app.shared.infrastructure.config.settings import settings


def to_e164(phone_number: str, country_code: str | None = None) -> str:
    """ "07488 566218" -> "+447488566218"; already-international numbers pass through."""
    code = country_code or settings.SMS_COUNTRY_CODE
    digits = "".join(ch for ch in phone_number if ch.isdigit() or ch == "+")

    if digits.startswith("+"):
        return digits
    if digits.startswith("00"):
        return "+" + digits[2:]
    # A national number: drop the trunk prefix before adding the country code.
    return code + digits.lstrip("0")
