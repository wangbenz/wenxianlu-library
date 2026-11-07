"""Only cultivation is migrated here; this is not a game or production config."""
import os

SECRET_KEY = "isolated-test-configuration-only"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "127.0.0.1"]
INSTALLED_APPS = ["cultivation", "rest_framework", "django_filters", "drf_spectacular"]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": os.environ["CULTIVATION_ISOLATED_DB"]}}
ROOT_URLCONF = "cultivation.tests.isolated_urls"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}
