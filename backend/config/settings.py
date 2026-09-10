import os
import sys
import warnings
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def load_local_env():
    """加载简单的 KEY=VALUE 配置，避免三天版额外引入 dotenv 依赖。"""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_local_env()

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-change-me")
AUDIT_IP_HASH_KEY = os.getenv("AUDIT_IP_HASH_KEY", SECRET_KEY)
DEBUG = os.getenv("DEBUG", "true").lower() == "true"
MODEL_CONFIG_ENCRYPTION_KEY = os.getenv("MODEL_CONFIG_ENCRYPTION_KEY", "")
ALLOW_PRIVATE_MODEL_ENDPOINTS = os.getenv("ALLOW_PRIVATE_MODEL_ENDPOINTS", "false").lower() == "true"
if ALLOW_PRIVATE_MODEL_ENDPOINTS and not DEBUG:
    warnings.warn(
        "ALLOW_PRIVATE_MODEL_ENDPOINTS 在非 DEBUG 环境中会被忽略，请使用公网 HTTPS 模型地址。",
        RuntimeWarning,
    )
ALLOWED_HOSTS = ["*"]
ALLOW_USER_REGISTRATION = os.getenv("ALLOW_USER_REGISTRATION", "true").lower() == "true"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework.authtoken",
    "api",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
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
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"

if os.getenv("DATABASE_ENGINE", "sqlite").lower() in {"postgres", "postgresql"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "knowledge_chat"),
            "USER": os.getenv("POSTGRES_USER", "knowledge_chat"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
            "HOST": os.getenv("POSTGRES_HOST", "127.0.0.1"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", str(BASE_DIR / "media")))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.TokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}

# 公开Token会出现在分享URL中；至少确保Django自身访问/错误日志不记录完整值。
# 生产反向代理也必须配置同等的路径脱敏规则。
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "application_secret_filter": {"()": "api.logging_filters.ApplicationSecretFilter"},
    },
    "handlers": {
        "safe_console": {
            "class": "logging.StreamHandler",
            "filters": ["application_secret_filter"],
        },
    },
    "loggers": {
        "django.server": {"handlers": ["safe_console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["safe_console"], "level": "WARNING", "propagate": False},
    },
}

MAX_UPLOAD_SIZE = 10 * 1024 * 1024

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/1")
IS_TESTING = "test" in sys.argv
CELERY_TASK_ALWAYS_EAGER = os.getenv(
    "CELERY_TASK_ALWAYS_EAGER",
    "true" if IS_TESTING else "false",
).lower() == "true"
CELERY_TASK_EAGER_PROPAGATES = os.getenv("CELERY_TASK_EAGER_PROPAGATES", "true").lower() == "true"
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_IGNORE_RESULT = False

DOCUMENT_TASK_LOCK_BACKEND = os.getenv(
    "DOCUMENT_TASK_LOCK_BACKEND",
    "memory" if IS_TESTING else "redis",
)
DOCUMENT_TASK_LOCK_REDIS_URL = os.getenv(
    "DOCUMENT_TASK_LOCK_REDIS_URL",
    "redis://127.0.0.1:6379/2",
)
DOCUMENT_TASK_SOFT_TIME_LIMIT = int(os.getenv("DOCUMENT_TASK_SOFT_TIME_LIMIT", "300"))
DOCUMENT_TASK_TIME_LIMIT = int(os.getenv("DOCUMENT_TASK_TIME_LIMIT", "360"))
DOCUMENT_TASK_LOCK_TIMEOUT = int(os.getenv("DOCUMENT_TASK_LOCK_TIMEOUT", "420"))

# 第十阶段应用公开访问保护。测试和本地DEBUG默认内存实现；生产显式使用Redis。
APPLICATION_RATE_LIMIT_BACKEND = os.getenv(
    "APPLICATION_RATE_LIMIT_BACKEND",
    "memory" if IS_TESTING or DEBUG else "redis",
).lower()
APPLICATION_RATE_LIMIT_REDIS_URL = os.getenv(
    "APPLICATION_RATE_LIMIT_REDIS_URL",
    "redis://127.0.0.1:6379/3",
)
APPLICATION_VISITOR_TOKEN_MAX_AGE = max(
    300,
    min(int(os.getenv("APPLICATION_VISITOR_TOKEN_MAX_AGE", "86400")), 604800),
)
PUBLIC_FRONTEND_URL = os.getenv("PUBLIC_FRONTEND_URL", "http://127.0.0.1:5173").rstrip("/")

# 第九阶段本地Cross-Encoder。默认不配置、不下载，不影响原检索链路。
_reranker_model_path = os.getenv("RERANKER_MODEL_PATH", "").strip()
RERANKER_MODEL_PATH = str(
    (BASE_DIR.parent / _reranker_model_path).resolve()
    if _reranker_model_path and not Path(_reranker_model_path).is_absolute()
    else _reranker_model_path
)
RERANKER_MODEL_REVISION = os.getenv("RERANKER_MODEL_REVISION", "").strip()
RERANKER_DEVICE = os.getenv("RERANKER_DEVICE", "cpu").strip() or "cpu"
RERANKER_MAX_LENGTH = max(64, min(int(os.getenv("RERANKER_MAX_LENGTH", "512")), 2048))
RERANKER_MAX_CHARS = max(256, min(int(os.getenv("RERANKER_MAX_CHARS", "4000")), 20000))
RERANKER_BATCH_SIZE = max(1, min(int(os.getenv("RERANKER_BATCH_SIZE", "8")), 32))
RERANKER_TIMEOUT_SECONDS = max(
    0.1,
    min(float(os.getenv("RERANKER_TIMEOUT_SECONDS", "3")), 30.0),
)
RERANKER_ALLOW_DOWNLOAD = os.getenv("RERANKER_ALLOW_DOWNLOAD", "false").lower() == "true"
