"""Configuración de Django.

Los valores se leen, en este orden, de variables de entorno y del archivo
``ambulancias.ini`` (sección [servidor] y [base_datos]). El instalador de
Windows crea ese archivo junto a la aplicación. Ver docs/INSTALACION.md.
"""

import configparser
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Orden de lectura (lo último prevalece): carpeta de datos de Windows (la crea el
# instalador y sobrevive a desinstalaciones), carpeta de la app, variable ABC_CONFIG.
_ini = configparser.ConfigParser()
_ini.read(
    [
        Path(os.environ.get("ProgramData", "C:/ProgramData")) / "CosteoABCAmbulancias" / "ambulancias.ini",
        BASE_DIR / "ambulancias.ini",
        os.environ.get("ABC_CONFIG", ""),
    ],
    encoding="utf-8-sig",
)


def cfg(clave: str, defecto: str = "", seccion: str = "servidor") -> str:
    """Variable de entorno ABC_<CLAVE> (o ABC_DB_<CLAVE> para [base_datos]), si no el .ini."""
    prefijo = "ABC_" if seccion == "servidor" else "ABC_DB_"
    return os.environ.get(f"{prefijo}{clave.upper()}", _ini.get(seccion, clave, fallback=defecto))


DATA_DIR = Path(cfg("data_dir", str(BASE_DIR / "datos")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

SECRET_KEY = cfg("secret_key", "solo-desarrollo-cambiar-en-produccion")
DEBUG = cfg("debug", "false").lower() in ("1", "true", "si", "sí")
ALLOWED_HOSTS = [h.strip() for h in cfg("allowed_hosts", "*").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in cfg("csrf_trusted_origins", "").split(",") if o.strip()]

# Sal para el hash del documento del paciente (no se guarda el número).
SAL_DOCUMENTO = cfg("sal_documento", SECRET_KEY)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "flota",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# --- Base de datos -----------------------------------------------------------
# sqlite (por defecto, red local): un único archivo en el servidor; todos los
#   equipos acceden por el navegador, nunca abren el archivo directamente.
#   WAL + transacciones IMMEDIATE + timeout evitan bloqueos con varios usuarios.
# postgresql (nube o servidor dedicado): motor recomendado con muchos usuarios.
DB_MOTOR = cfg("motor", "sqlite", "base_datos").lower()
if DB_MOTOR in ("postgres", "postgresql"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": cfg("nombre", "ambulancias_abc", "base_datos"),
            "USER": cfg("usuario", "ambulancias", "base_datos"),
            "PASSWORD": cfg("clave", "", "base_datos"),
            "HOST": cfg("host", "localhost", "base_datos"),
            "PORT": cfg("puerto", "5432", "base_datos"),
            "CONN_MAX_AGE": 60,
            "OPTIONS": {"sslmode": cfg("sslmode", "prefer", "base_datos")},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": cfg("archivo", str(DATA_DIR / "ambulancias_abc.sqlite3"), "base_datos"),
            "OPTIONS": {
                "timeout": 30,
                "transaction_mode": "IMMEDIATE",
                "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON;",
            },
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "es-co"
TIME_ZONE = "America/Bogota"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

STATIC_URL = "static/"
STATIC_ROOT = DATA_DIR / "static"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "flota:dashboard"
LOGOUT_REDIRECT_URL = "login"

DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024

# Respaldos
BACKUP_DIR = Path(cfg("backup_dir", str(DATA_DIR / "respaldos")))
BACKUP_RETENCION = int(cfg("backup_retencion", "30"))

SESSION_COOKIE_AGE = 60 * 60 * 10
if cfg("https", "false").lower() == "true":
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "archivo": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(DATA_DIR / "ambulancias_abc.log"),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 5,
            "encoding": "utf-8",
        },
        "consola": {"class": "logging.StreamHandler"},
    },
    "root": {"handlers": ["archivo", "consola"], "level": "INFO"},
}
