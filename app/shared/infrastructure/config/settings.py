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
    CORS_ALLOWED_ORIGINS: str = "*"
    ALLOWED_HOSTS: str = "*"
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
    BOOKING_HORIZON_DAYS: int = 365
    # "console" prints OTP codes and deposit links to the log (local development only).
    # "null" records that a message was sent without ever logging its contents.
    NOTIFICATION_BACKEND: str = "null"
    # Only trust X-Forwarded-For for rate limiting when running behind a proxy you control.
    TRUST_PROXY_HEADERS: bool = False
    SQL_ECHO: bool = False

    class Config:
        env_file = ".env"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        if self.CORS_ALLOWED_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",")]

    @property
    def allowed_hosts_list(self) -> list[str]:
        if self.ALLOWED_HOSTS == "*":
            return ["*"]
        return [host.strip() for host in self.ALLOWED_HOSTS.split(",")]


settings = Settings()
