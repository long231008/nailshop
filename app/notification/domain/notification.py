from dataclasses import dataclass


@dataclass(frozen=True)
class Notification:
    """A message addressed to a person, on whichever channel we can reach them."""

    subject: str
    body: str
    phone_number: str | None = None
    email: str | None = None

    @property
    def destination(self) -> str | None:
        return self.phone_number or self.email


def mask_destination(destination: str | None) -> str:
    """Render a phone/email for logs without exposing the whole identifier."""
    if not destination:
        return "unknown"
    if "@" in destination:
        local, _, domain = destination.partition("@")
        head = local[:2] if len(local) > 2 else local[:1]
        return f"{head}***@{domain}"
    return f"***{destination[-4:]}" if len(destination) > 4 else "***"
