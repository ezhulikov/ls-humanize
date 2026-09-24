"""Shared WordPress client helpers for every humanizer script.

All credential loading, authentication, and HTTP access lives here so the
publishing and restore tools cannot drift apart. Every non-GET request is
gated on WP_ENABLE_WRITES=true.
"""

import base64
import json
import os
import urllib.request
from pathlib import Path


HERE = Path(__file__).resolve().parent
ENV_FILE = HERE.parent / ".env"
DEFAULT_USERNAME_ENV = "WP_USERNAME_VS"
DEFAULT_PASSWORD_ENV = "WP_APP_PASSWORD_VS"
DEFAULT_USER_AGENT = "Humanizer WordPress client/1.0"
PUBLIC_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
)


class WriteNotEnabledError(RuntimeError):
    """Raised when a write is attempted without WP_ENABLE_WRITES=true."""


def load_env(env_file=ENV_FILE):
    """Read KEY=VALUE pairs from .env, letting WP_* process variables win."""
    values = {}
    env_file = Path(env_file)
    if env_file.exists():
        with env_file.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip().strip('"').strip("'")
    values.update({key: value for key, value in os.environ.items() if key.startswith("WP_")})
    return values


def basic_auth(env, username_env=DEFAULT_USERNAME_ENV, password_env=DEFAULT_PASSWORD_ENV):
    credentials = f"{env[username_env]}:{env[password_env]}".encode("utf-8")
    return "Basic " + base64.b64encode(credentials).decode("ascii")


def writes_enabled():
    return os.environ.get("WP_ENABLE_WRITES", "").lower() == "true"


def ensure_writes_enabled():
    if not writes_enabled():
        raise WriteNotEnabledError("Set WP_ENABLE_WRITES=true for this intentional live write")


def request_json(url, authorization, method="GET", payload=None, user_agent=DEFAULT_USER_AGENT):
    """Authenticated JSON request. Any non-GET method requires WP_ENABLE_WRITES."""
    if method != "GET":
        ensure_writes_enabled()
    headers = {
        "Authorization": authorization,
        "Accept": "application/json",
        "User-Agent": user_agent,
    }
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json; charset=utf-8"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, headers=headers, data=body, method=method)
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_public(url):
    """Unauthenticated GET of a public page. Returns (status, body_bytes)."""
    request = urllib.request.Request(url, headers={"User-Agent": PUBLIC_USER_AGENT})
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.status, response.read()
