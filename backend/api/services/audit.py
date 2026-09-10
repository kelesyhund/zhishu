import hashlib
import hmac
import re
import uuid

from django.conf import settings

from api.models import AuditEvent


SAFE_METADATA_KEYS = {
    "capability",
    "changed_fields",
    "member_role",
    "target_user_id",
    "target_membership_id",
    "status",
    "source",
    "reason_code",
}
SENSITIVE_KEY_PARTS = ("key", "token", "secret", "authorization", "password", "content", "prompt")


def _safe_request_id(request) -> str:
    value = str(request.headers.get("X-Request-ID", "")).strip()
    if value and len(value) <= 64 and re.fullmatch(r"[A-Za-z0-9._:-]+", value):
        return value
    return uuid.uuid4().hex


def _ip_hash(request) -> str:
    ip = str(request.META.get("REMOTE_ADDR", "")).strip()
    if not ip:
        return ""
    key = str(getattr(settings, "AUDIT_IP_HASH_KEY", settings.SECRET_KEY)).encode("utf-8")
    return hmac.new(key, ip.encode("utf-8"), hashlib.sha256).hexdigest()


def safe_metadata(metadata=None) -> dict:
    result = {}
    for key, value in (metadata or {}).items():
        normalized_key = str(key)[:80]
        if normalized_key not in SAFE_METADATA_KEYS:
            continue
        if any(part in normalized_key.lower() for part in SENSITIVE_KEY_PARTS):
            continue
        if isinstance(value, (bool, int, float)) or value is None:
            result[normalized_key] = value
        elif isinstance(value, str):
            result[normalized_key] = value[:200]
        elif isinstance(value, (list, tuple)):
            result[normalized_key] = [str(item)[:80] for item in value[:20]]
    return result


def record_audit_event(
    request,
    *,
    organization,
    workspace=None,
    action: str,
    resource_type: str = "",
    resource_id="",
    result=AuditEvent.Result.SUCCESS,
    metadata=None,
):
    try:
        return AuditEvent.objects.create(
            organization=organization,
            workspace=workspace,
            actor=request.user if getattr(request.user, "is_authenticated", False) else None,
            action=action[:100],
            resource_type=resource_type[:80],
            resource_id=str(resource_id)[:80],
            result=result,
            request_id=_safe_request_id(request),
            ip_hash=_ip_hash(request),
            metadata=safe_metadata(metadata),
        )
    except Exception:
        # 审计不可阻断主业务；生产环境应由日志采集器监控数据库写入异常。
        return None

