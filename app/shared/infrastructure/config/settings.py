from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    PAYMENT_WEBHOOK_SECRET: str = "dev-payment-webhook-secret"
    PAYMENT_CHECKOUT_BASE_URL: str = "https://payments.example.com/checkout"
    CLOUDINARY_CLOUD_NAME: str
    CLOUDINARY_API_KEY: str
    CLOUDINARY_API_SECRET: str
    # Where this API is reachable from the browser (used for locally stored images).
    BACKEND_PUBLIC_URL: str = "http://localhost:8000"
    GOOGLE_OAUTH_CLIENT_ID: str
    GOOGLE_OAUTH_CLIENT_SECRET: str
    GOOGLE_OAUTH_REDIRECT_URI: str = "http://localhost:8000/app/auth/google/callback"
    # Optional - Facebook login stays disabled until both are set.
    FACEBOOK_APP_ID: str = ""
    FACEBOOK_APP_SECRET: str = ""
    FACEBOOK_OAUTH_REDIRECT_URI: str = "http://localhost:8000/app/auth/facebook/callback"
    FRONTEND_URL: str = "http://localhost:5173"
    # Which websites may call this API from a browser, and which Host headers
    # this API answers to. Defaults cover local development only - production
    # must name its real domains, never "*".
    CORS_ALLOWED_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    ALLOWED_HOSTS: str = "localhost,127.0.0.1"
    # Business timezone. Opening hours, "today" and daily counters are computed in this
    # zone, not UTC, so a UK summer day still starts at 00:00 local time.
    SHOP_TIMEZONE: str = "Europe/London"
    # The whole calendar is open year-round inside these local hours; admins and
    # authorised staff lock individual time ranges instead of publishing rosters.
    SHOP_OPEN_HOUR: int = 9
    SHOP_CLOSE_HOUR: int = 18
    # Last accepted start time. A long treatment starting then may run past
    # closing - that's the salon's explicit choice.
    SHOP_LAST_BOOKING_HOUR: int = 17
    SHOP_LAST_BOOKING_MINUTE: int = 30
    # Advance bookings live on the capacity ledger (design doc 3.3b): day D takes
    # bookings until BOOKING_CLOSE_HOUR local time on D-1, then freezes so the
    # nightly allocation can assign technicians to a demand that no longer moves.
    BOOKING_HORIZON_DAYS: int = 14
    BOOKING_CLOSE_HOUR: int = 21
    # Share of expected technicians that may be sold per 15' slot; the remainder
    # is the walk-in lane plus the staffing-surprise cushion.
    CAP_FILL: float = 0.85
    # "console" prints OTP codes and deposit links to the log (local development only).
    # "null" records that a message was sent without ever logging its contents.
    # "live" delivers for real: SMS to phone numbers, email to addresses.
    NOTIFICATION_BACKEND: str = "null"
    # Twilio (SMS). SENDER may be a purchased number or an approved alphanumeric
    # sender ID such as "Nailzinc".
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_SENDER: str = ""
    # Local numbers are stored as customers type them ("07..."); carriers need
    # E.164 ("+447...").
    SMS_COUNTRY_CODE: str = "+44"
    # Email. Either SendGrid's API, or plain SMTP - which works with the free
    # tiers of Brevo, Resend, Mailgun, or even a Gmail app password.
    SENDGRID_API_KEY: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    EMAIL_FROM_ADDRESS: str = ""
    EMAIL_FROM_NAME: str = "Nailzinc"
    # SMS costs money per message, email is effectively free. When a customer
    # has both, reach them by email unless told otherwise.
    PREFER_EMAIL_OVER_SMS: bool = True
    # Only trust X-Forwarded-For for rate limiting when running behind a proxy you control.
    TRUST_PROXY_HEADERS: bool = False
    SQL_ECHO: bool = False

    class Config:
        env_file = ".env"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        if self.CORS_ALLOWED_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]

    @property
    def allowed_hosts_list(self) -> list[str]:
        if self.ALLOWED_HOSTS == "*":
            return ["*"]
        # Health checks and load balancers often probe by IP or without a Host.
        return [host.strip() for host in self.ALLOWED_HOSTS.split(",") if host.strip()]

    @property
    def insecure_wildcards(self) -> list[str]:
        """Settings that are fine locally but must never ship to production."""
        problems = []
        if self.CORS_ALLOWED_ORIGINS == "*":
            problems.append("CORS_ALLOWED_ORIGINS=*")
        if self.ALLOWED_HOSTS == "*":
            problems.append("ALLOWED_HOSTS=*")
        if self.NOTIFICATION_BACKEND == "console":
            problems.append("NOTIFICATION_BACKEND=console (OTP codes go to the log)")
        if self.NOTIFICATION_BACKEND == "null":
            problems.append("NOTIFICATION_BACKEND=null (nothing is actually delivered)")
        if len(self.JWT_SECRET_KEY) < 32:
            problems.append("JWT_SECRET_KEY is shorter than 32 characters")
        if self.PAYMENT_WEBHOOK_SECRET == "dev-payment-webhook-secret":
            problems.append(
                "PAYMENT_WEBHOOK_SECRET is the development default (forged payment "
                "webhooks would be accepted)"
            )
        return problems


settings = Settings()
