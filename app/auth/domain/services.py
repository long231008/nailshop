import secrets


def generate_otp_code(length: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(length))
