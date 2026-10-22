"""Configuración de la herramienta de Costos ABC en salud.

Todos los valores sensibles se leen de variables de entorno (ver .env.example).
"""
import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent


def cargar_env(ruta):
    """Lee un archivo .env (CLAVE=valor) sin sobrescribir variables ya definidas en el sistema."""
    if not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8-sig").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


cargar_env(BASE_DIR / ".env")


def env_bool(nombre, defecto=False):
    return os.environ.get(nombre, str(defecto)).strip().lower() in {"1", "true", "si", "sí", "yes"}


DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if os.environ.get("DJANGO_ENV") == "production":
        raise RuntimeError("Defina DJANGO_SECRET_KEY en producción.")
    SECRET_KEY = "solo-desarrollo-no-usar-en-produccion"

# Clave independiente para seudonimizar documentos de pacientes (HMAC-SHA256).
PSEUDONIMO_KEY = os.environ.get("PSEUDONIMO_KEY", SECRET_KEY)

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "core",
    "modelo",
    "cargas",
    "motor",
    "analisis",
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
    "core.middleware.InstitucionMiddleware",
]

ROOT_URLCONF = "abc_salud.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.institucion",
            ],
        },
    },
]

WSGI_APPLICATION = "abc_salud.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=int(os.environ.get("DB_CONN_MAX_AGE", "60")),
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es-co"
TIME_ZONE = os.environ.get("TIME_ZONE", "America/Bogota")
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = False  # los montos usan los filtros de core/templatetags/formato.py

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"
        if not DEBUG
        else "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}
MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# SQLite no crea restricciones únicas con NULLS NOT DISTINCT; en PostgreSQL 15+ sí. La aplicación usa update_or_create.
SILENCED_SYSTEM_CHECKS = ["models.W047"]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "core:seleccionar_institucion"
LOGOUT_REDIRECT_URL = "login"

# --- Seguridad -------------------------------------------------------------
SESSION_COOKIE_AGE = int(os.environ.get("SESSION_COOKIE_AGE", 30 * 60))  # 30 min de inactividad
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
# USAR_HTTPS=0 solo para redes internas sin certificado (el instalador lo pregunta).
if not DEBUG and os.environ.get("DJANGO_ENV") == "production" and env_bool("USAR_HTTPS", True):
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

LOGIN_MAX_INTENTOS = int(os.environ.get("LOGIN_MAX_INTENTOS", 5))
LOGIN_BLOQUEO_SEGUNDOS = int(os.environ.get("LOGIN_BLOQUEO_SEGUNDOS", 15 * 60))

# --- Cargas de archivos ----------------------------------------------------
CARGA_MAX_MB = int(os.environ.get("CARGA_MAX_MB", 25))
DATA_UPLOAD_MAX_MEMORY_SIZE = CARGA_MAX_MB * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
CARGA_EXTENSIONES = {".xlsx", ".xlsm", ".xls", ".csv"}

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
