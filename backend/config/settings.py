import os
import sys
import warnings
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
IS_TESTING = "test" in sys.argv


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

ENVIRONMENT = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = ENVIRONMENT == "production"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-change-me")
if IS_PRODUCTION and SECRET_KEY == "dev-only-change-me":
    raise RuntimeError("生产环境必须配置独立的 DJANGO_SECRET_KEY")
AUDIT_IP_HASH_KEY = os.getenv("AUDIT_IP_HASH_KEY", SECRET_KEY)
DEBUG = os.getenv("DEBUG", "false" if IS_PRODUCTION else "true").lower() == "true"
MODEL_CONFIG_ENCRYPTION_KEY = os.getenv("MODEL_CONFIG_ENCRYPTION_KEY", "")
ALLOW_PRIVATE_MODEL_ENDPOINTS = os.getenv("ALLOW_PRIVATE_MODEL_ENDPOINTS", "false").lower() == "true"
if ALLOW_PRIVATE_MODEL_ENDPOINTS and not DEBUG:
    warnings.warn(
        "ALLOW_PRIVATE_MODEL_ENDPOINTS 在非 DEBUG 环境中会被忽略，请使用公网 HTTPS 模型地址。",
        RuntimeWarning,
    )
ALLOWED_HOSTS = [item.strip() for item in os.getenv(
    "ALLOWED_HOSTS", "127.0.0.1,localhost" if not IS_PRODUCTION else ""
).split(",") if item.strip()]
if IS_PRODUCTION and not ALLOWED_HOSTS:
    raise RuntimeError("生产环境必须配置 ALLOWED_HOSTS")
ALLOW_USER_REGISTRATION = os.getenv("ALLOW_USER_REGISTRATION", "true").lower() == "true"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "corsheaders",
    "rest_framework",
    "rest_framework.authtoken",
    "api",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "api.middleware.ObservabilityMiddleware",
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
            "OPTIONS": {"connect_timeout": int(os.getenv("POSTGRES_CONNECT_TIMEOUT", "3"))},
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": Path(os.getenv("SQLITE_DATABASE_PATH", str(BASE_DIR / "db.sqlite3"))),
        }
    }

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

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", str(BASE_DIR / "media")))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = [item.strip() for item in os.getenv(
    "CSRF_TRUSTED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",") if item.strip()]
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "true" if IS_PRODUCTION else "false").lower() == "true"
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = os.getenv("CSRF_COOKIE_SECURE", "true" if IS_PRODUCTION else "false").lower() == "true"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "true" if IS_PRODUCTION else "false").lower() == "true"
SECURE_REDIRECT_EXEMPT = [r"^health/"]
SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000" if IS_PRODUCTION else "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = IS_PRODUCTION
SECURE_HSTS_PRELOAD = IS_PRODUCTION
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "DENY"

if os.getenv("CACHE_REDIS_URL", "").strip():
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": os.getenv("CACHE_REDIS_URL"),
        }
    }

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}

EMAIL_BACKEND = os.getenv(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend" if not IS_TESTING else "django.core.mail.backends.locmem.EmailBackend",
)
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "知枢 <no-reply@localhost>")
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "http://127.0.0.1:5173").rstrip("/")
INVITATION_EXPIRE_HOURS = max(1, min(int(os.getenv("INVITATION_EXPIRE_HOURS", "168")), 720))
PASSWORD_RESET_EXPIRE_SECONDS = max(300, min(int(os.getenv("PASSWORD_RESET_EXPIRE_SECONDS", "3600")), 86400))
PASSWORD_RESET_TIMEOUT = PASSWORD_RESET_EXPIRE_SECONDS
LOGIN_RATE_LIMIT_ATTEMPTS = max(3, min(int(os.getenv("LOGIN_RATE_LIMIT_ATTEMPTS", "8")), 100))
LOGIN_RATE_LIMIT_WINDOW_SECONDS = max(60, min(int(os.getenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "900")), 86400))

# 公开Token会出现在分享URL中；至少确保Django自身访问/错误日志不记录完整值。
# 生产反向代理也必须配置同等的路径脱敏规则。
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "application_secret_filter": {"()": "api.logging_filters.ApplicationSecretFilter"},
        "observability_context": {"()": "api.logging_filters.ObservabilityContextFilter"},
    },
    "formatters": {
        "json": {"()": "api.logging_filters.JsonLogFormatter"},
        "plain": {"format": "%(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "safe_console": {
            "class": "logging.StreamHandler",
            "filters": ["application_secret_filter", "observability_context"],
            "formatter": "json" if IS_PRODUCTION else "plain",
        },
    },
    "loggers": {
        "django.server": {"handlers": ["safe_console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["safe_console"], "level": "WARNING", "propagate": False},
        "api.observability": {
            "handlers": ["safe_console"],
            "level": "WARNING" if IS_TESTING else "INFO",
            "propagate": False,
        },
        "api.tasks": {"handlers": ["safe_console"], "level": "INFO", "propagate": False},
    },
    "root": {
        "handlers": ["safe_console"],
        "level": "WARNING" if IS_TESTING else os.getenv("LOG_LEVEL", "INFO"),
    },
}

MAX_UPLOAD_SIZE = 10 * 1024 * 1024

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://127.0.0.1:6379/1")
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
CELERY_BEAT_SCHEDULE = {
    "stage15-reconcile-stale-document-tasks": {
        "task": "api.tasks.reconcile_stale_document_tasks",
        "schedule": float(os.getenv("STALE_TASK_SCAN_INTERVAL_SECONDS", "60")),
    },
}
WORKER_HEARTBEAT_TTL_SECONDS = max(30, int(os.getenv("WORKER_HEARTBEAT_TTL_SECONDS", "90")))

OTEL_ENABLED = os.getenv("OTEL_ENABLED", "false").lower() == "true"
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "zhishu-backend")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
OTEL_EXPORT_TIMEOUT_SECONDS = max(1, min(int(os.getenv("OTEL_EXPORT_TIMEOUT_SECONDS", "3")), 10))
METRICS_PORT = max(1024, min(int(os.getenv("METRICS_PORT", "9101")), 65535))

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
STALE_TASK_THRESHOLD_SECONDS = max(
    DOCUMENT_TASK_TIME_LIMIT + 60,
    int(os.getenv("STALE_TASK_THRESHOLD_SECONDS", "420")),
)

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

# 第十六阶段向量迁移开关。默认保留旧 JSON/Python 链路，便于无损灰度和回滚。
VECTOR_WRITE_MODE = os.getenv("VECTOR_WRITE_MODE", "LEGACY").strip().upper()
VECTOR_READ_MODE = os.getenv("VECTOR_READ_MODE", "LEGACY").strip().upper()
VECTOR_SHADOW_SAMPLE_RATE = float(os.getenv("VECTOR_SHADOW_SAMPLE_RATE", "0"))
VECTOR_SEARCH_MODE = os.getenv("VECTOR_SEARCH_MODE", "EXACT").strip().upper()
VECTOR_HNSW_EF_SEARCH = max(1, min(int(os.getenv("VECTOR_HNSW_EF_SEARCH", "100")), 1000))
if VECTOR_WRITE_MODE not in {"LEGACY", "DUAL", "PGVECTOR"}:
    raise RuntimeError("VECTOR_WRITE_MODE 必须为 LEGACY、DUAL 或 PGVECTOR")
if VECTOR_READ_MODE not in {"LEGACY", "SHADOW", "PGVECTOR"}:
    raise RuntimeError("VECTOR_READ_MODE 必须为 LEGACY、SHADOW 或 PGVECTOR")
if VECTOR_SEARCH_MODE not in {"EXACT", "HNSW"}:
    raise RuntimeError("VECTOR_SEARCH_MODE 必须为 EXACT 或 HNSW")
if not 0 <= VECTOR_SHADOW_SAMPLE_RATE <= 1:
    raise RuntimeError("VECTOR_SHADOW_SAMPLE_RATE 必须在 0 到 1 之间")
if VECTOR_WRITE_MODE == "PGVECTOR" and VECTOR_READ_MODE != "PGVECTOR":
    raise RuntimeError("PGVECTOR 单写必须与 PGVECTOR 读取组合")
if os.getenv("DATABASE_ENGINE", "sqlite").lower() not in {"postgres", "postgresql"} and (
    VECTOR_WRITE_MODE != "LEGACY" or VECTOR_READ_MODE != "LEGACY"
):
    raise RuntimeError("非PostgreSQL环境只能使用LEGACY向量读写模式")
