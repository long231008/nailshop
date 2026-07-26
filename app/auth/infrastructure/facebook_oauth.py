from urllib.parse import urlencode

import requests

from app.shared.infrastructure.config.settings import settings

FACEBOOK_AUTH_ENDPOINT = "https://www.facebook.com/v19.0/dialog/oauth"
FACEBOOK_TOKEN_ENDPOINT = "https://graph.facebook.com/v19.0/oauth/access_token"
FACEBOOK_PROFILE_ENDPOINT = "https://graph.facebook.com/v19.0/me"


class FacebookNotConfiguredError(Exception):
    pass


class FacebookTokenExchangeError(Exception):
    pass


class FacebookProfileError(Exception):
    pass


def is_facebook_configured() -> bool:
    return bool(settings.FACEBOOK_APP_ID and settings.FACEBOOK_APP_SECRET)


def build_facebook_authorization_url(state: str) -> str:
    if not is_facebook_configured():
        raise FacebookNotConfiguredError()
    params = {
        "client_id": settings.FACEBOOK_APP_ID,
        "redirect_uri": settings.FACEBOOK_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": "email,public_profile",
        "state": state,
    }
    return f"{FACEBOOK_AUTH_ENDPOINT}?{urlencode(params)}"


def exchange_code_for_access_token(code: str) -> str:
    response = requests.get(
        FACEBOOK_TOKEN_ENDPOINT,
        params={
            "client_id": settings.FACEBOOK_APP_ID,
            "client_secret": settings.FACEBOOK_APP_SECRET,
            "redirect_uri": settings.FACEBOOK_OAUTH_REDIRECT_URI,
            "code": code,
        },
        timeout=10,
    )
    if response.status_code != 200:
        raise FacebookTokenExchangeError(response.text)
    return response.json()["access_token"]


def fetch_user_profile(access_token: str) -> dict:
    """Email (only if the user confirmed it with Facebook) plus their name."""
    response = requests.get(
        FACEBOOK_PROFILE_ENDPOINT,
        params={"fields": "id,email,first_name,last_name", "access_token": access_token},
        timeout=10,
    )
    if response.status_code != 200:
        raise FacebookProfileError(response.text)
    return response.json()
