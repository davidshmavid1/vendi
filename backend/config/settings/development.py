"""Local development. Reads backend/.env if present."""

from config.env import database_config, env_list, env_str, load_env_file

load_env_file()

from .base import *  # noqa: E402, F403

DEBUG = True
SECRET_KEY = env_str("DJANGO_SECRET_KEY", "django-insecure-development-only-key")
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

DATABASES = {"default": database_config(require_local=True)}

API_DOCS_ENABLED = True
