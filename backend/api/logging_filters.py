import logging
import json
import re
from datetime import datetime, timezone

from django.conf import settings

from .observability import current_log_context, current_request_id, current_trace_id


PUBLIC_TOKEN_PATTERN = re.compile(r"kc_pub_[A-Fa-f0-9]{12}_[A-Za-z0-9_-]{16,}")
APPLICATION_KEY_PATTERN = re.compile(r"kc_app_[A-Fa-f0-9]{12}_[A-Za-z0-9_-]{16,}")
INVITATION_PATH_PATTERN = re.compile(r"(/api/invitations/)[A-Za-z0-9_-]{40,}")
RESET_TOKEN_PATTERN = re.compile(r"(/api/auth/(?:password-reset|email-verification)/[^\s?]+/)[A-Za-z0-9_-]{20,}")
MODEL_API_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
AUTHORIZATION_PATTERN = re.compile(r"(?i)\b(?:authorization|cookie)\s*[:=]\s*(?!%[a-z])[^\s,;]+")
SENSITIVE_KEYS = {
    "password", "password1", "password2", "token", "cookie", "authorization",
    "api_key", "encrypted_api_key", "secret", "fernet_key", "sessionid",
}


def redact_application_secrets(value):
    if isinstance(value, str):
        value = PUBLIC_TOKEN_PATTERN.sub("kc_pub_<redacted>", value)
        value = APPLICATION_KEY_PATTERN.sub("kc_app_<redacted>", value)
        value = INVITATION_PATH_PATTERN.sub(r"\1<redacted>", value)
        value = RESET_TOKEN_PATTERN.sub(r"\1<redacted>", value)
        value = MODEL_API_KEY_PATTERN.sub("<redacted>", value)
        return AUTHORIZATION_PATTERN.sub("<redacted>", value)
    if isinstance(value, tuple):
        return tuple(redact_application_secrets(item) for item in value)
    if isinstance(value, list):
        return [redact_application_secrets(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "<redacted>" if str(key).lower() in SENSITIVE_KEYS else redact_application_secrets(item)
            for key, item in value.items()
        }
    return value


class ApplicationSecretFilter(logging.Filter):
    def filter(self, record):
        record.msg = redact_application_secrets(record.msg)
        record.args = redact_application_secrets(record.args)
        return True


class ObservabilityContextFilter(logging.Filter):
    def filter(self, record):
        for key, value in current_log_context().items():
            if not hasattr(record, key):
                setattr(record, key, value)
        if not getattr(record, "request_id", ""):
            record.request_id = current_request_id()
        if not getattr(record, "trace_id", ""):
            record.trace_id = current_trace_id()
        return True


class JsonLogFormatter(logging.Formatter):
    FIELDS = (
        "event", "request_id", "trace_id", "method", "route", "status_code", "duration_ms",
        "organization_id", "workspace_id", "user_id", "knowledge_base_id", "document_id",
        "task_id", "model_config_id", "error_code",
    )

    def format(self, record):
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": getattr(settings, "OTEL_SERVICE_NAME", "zhishu-backend"),
            "environment": getattr(settings, "ENVIRONMENT", "development"),
            "logger": record.name,
            "message": redact_application_secrets(record.getMessage()),
        }
        for field in self.FIELDS:
            value = getattr(record, field, None)
            if value not in (None, ""):
                payload[field] = redact_application_secrets(value)
        if record.exc_info:
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
