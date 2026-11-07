"""问仙录资料库配置：只加载资料定义与维护所需框架，不执行游戏逻辑。"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.environ.get("WENXIANLU_DEBUG", "True").lower() == "true"
_DEV_KEY = "wenxianlu-local-dev-insecure-do-not-use-in-production"
SECRET_KEY = os.environ.get("SECRET_KEY", _DEV_KEY)
if not DEBUG and SECRET_KEY == _DEV_KEY:
    raise ImproperlyConfigured("非调试模式必须通过 SECRET_KEY 环境变量设置密钥。")
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "cultivation.apps.CultivationConfig",
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "drf_spectacular_sidecar",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "server.library_urls"
WSGI_APPLICATION = "server.wsgi.application"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

DATABASE_PATH = Path(os.environ.get("WENXIANLU_DB_PATH", BASE_DIR / "var/wenxianlu-library.sqlite3"))
if not DATABASE_PATH.is_absolute():
    DATABASE_PATH = BASE_DIR / DATABASE_PATH
DATABASE_PATH = DATABASE_PATH.resolve()
_UPSTREAM_DATABASE = BASE_DIR / "db.sqlite3"
if DATABASE_PATH == _UPSTREAM_DATABASE.resolve() or (
    DATABASE_PATH.exists() and _UPSTREAM_DATABASE.exists() and DATABASE_PATH.samefile(_UPSTREAM_DATABASE)
):
    raise ImproperlyConfigured("资料库模式不能使用上游对照数据库 db.sqlite3；请选择独立数据库。")
DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": DATABASE_PATH}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = Path(os.environ.get("WENXIANLU_STATIC_ROOT", BASE_DIR / "var/staticfiles"))
if not STATIC_ROOT.is_absolute():
    STATIC_ROOT = BASE_DIR / STATIC_ROOT
STATIC_ROOT = STATIC_ROOT.resolve()

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
}
SPECTACULAR_SETTINGS = {
    "TITLE": "问仙录资料库",
    "DESCRIPTION": "修仙基础定义的只读接口。支持中文名称精确和包含查询，不提供语义搜索。",
    "VERSION": "v1",
    "SERVERS": [{"url": "/", "description": "当前资料库服务"}],
    "SERVE_INCLUDE_SCHEMA": False,
    "SWAGGER_UI_DIST": "SIDECAR",
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
    "ENUM_NAME_OVERRIDES": {
        "CultivationItemTypeEnum": "cultivation.models.Item.Type",
        "CultivationEffectTypeEnum": ["restore_hp"],
    },
}
