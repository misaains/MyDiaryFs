import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

ENV_FILE = Path(os.getenv("MYDIARY_ENV_FILE", str(Path(__file__).with_name(".env")))).expanduser()
REQUIRED = (
    "TELEGRAM_BOT_TOKEN", "GROQ_API_KEY",
    "FATSECRET_CONSUMER_KEY", "FATSECRET_CONSUMER_SECRET",
)


def read_settings(path=ENV_FILE):
    values = dotenv_values(path, interpolate=False) if path.is_file() else {}
    settings = {key: (value or "").strip() for key, value in values.items()}
    # Preserve support for native .env files and environment-only deployments.
    for key in (*REQUIRED, "ALLOWED_TELEGRAM_IDS", "TELEGRAM_PROXY", "OUTBOUND_PROXY"):
        if key in os.environ:
            settings[key] = os.environ[key].strip()
    settings.setdefault("TELEGRAM_PROXY", "")
    settings.setdefault("OUTBOUND_PROXY", "")
    return settings


def parse_allowed_ids(value):
    parts = value.split(",")
    if not parts or any(not re.fullmatch(r"[1-9][0-9]*", part.strip()) for part in parts):
        raise ValueError("ALLOWED_TELEGRAM_IDS must contain positive numeric IDs separated by commas")
    return frozenset(int(part.strip()) for part in parts)


def configuration_errors(settings):
    errors = [key for key in REQUIRED if not settings.get(key) or settings[key].startswith("your_")]
    token = settings.get("TELEGRAM_BOT_TOKEN", "")
    if token and not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token):
        if "TELEGRAM_BOT_TOKEN" not in errors:
            errors.append("TELEGRAM_BOT_TOKEN")
    try:
        parse_allowed_ids(settings.get("ALLOWED_TELEGRAM_IDS", ""))
    except ValueError:
        errors.append("ALLOWED_TELEGRAM_IDS")
    for key in ("TELEGRAM_PROXY", "OUTBOUND_PROXY"):
        value = settings.get(key, "")
        if not value:
            continue
        try:
            proxy = urlsplit(value)
            valid = (proxy.scheme == "http" and proxy.hostname and proxy.port
                     and proxy.path in ("", "/") and not proxy.query and not proxy.fragment
                     and not any(character.isspace() for character in value))
        except ValueError:
            valid = False
        if not valid:
            errors.append(key)
    return errors


SETTINGS = read_settings()
TELEGRAM_BOT_TOKEN = SETTINGS.get("TELEGRAM_BOT_TOKEN", "")
GROQ_API_KEY = SETTINGS.get("GROQ_API_KEY", "")
FATSECRET_CONSUMER_KEY = SETTINGS.get("FATSECRET_CONSUMER_KEY", "")
FATSECRET_CONSUMER_SECRET = SETTINGS.get("FATSECRET_CONSUMER_SECRET", "")
TELEGRAM_PROXY = SETTINGS.get("TELEGRAM_PROXY", "")
OUTBOUND_PROXY = SETTINGS.get("OUTBOUND_PROXY", "")


def apply_proxy_environment(settings):
    if settings.get("OUTBOUND_PROXY"):
        os.environ["HTTP_PROXY"] = os.environ["HTTPS_PROXY"] = settings["OUTBOUND_PROXY"]
        os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1,host.docker.internal")


def validate_runtime():
    errors = configuration_errors(SETTINGS)
    if errors:
        raise RuntimeError("Configuration is incomplete: " + ", ".join(errors))
    apply_proxy_environment(SETTINGS)
    return parse_allowed_ids(SETTINGS["ALLOWED_TELEGRAM_IDS"])
