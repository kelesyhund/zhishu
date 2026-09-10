import hashlib
import hmac
import secrets

from django.conf import settings
from django.core import signing

from api.models import ApplicationPublicAccess


VISITOR_SALT = "knowledge-chat.application-visitor.v1"


class PublicAccessError(Exception):
    def __init__(self, message="公开访问链接无效或已停用", error_code="PUBLIC_ACCESS_INVALID"):
        super().__init__(message)
        self.message = message
        self.error_code = error_code


def token_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def rotate_public_token(application, *, enabled=True):
    prefix = secrets.token_hex(6)
    token = f"kc_pub_{prefix}_{secrets.token_urlsafe(32)}"
    access, _ = ApplicationPublicAccess.objects.update_or_create(
        application=application,
        defaults={
            "enabled": enabled,
            "token_prefix": prefix,
            "token_digest": token_digest(token),
            "token_last4": token[-4:],
        },
    )
    return access, token


def authenticate_public_token(token: str):
    parts = token.split("_", 3)
    if len(parts) != 4 or parts[:2] != ["kc", "pub"]:
        raise PublicAccessError()
    access = ApplicationPublicAccess.objects.select_related(
        "application__current_published_version"
    ).filter(token_prefix=parts[2]).first()
    if not access or not hmac.compare_digest(access.token_digest, token_digest(token)):
        raise PublicAccessError()
    application = access.application
    if not access.enabled or application.status != application.Status.PUBLISHED:
        raise PublicAccessError()
    if not application.current_published_version_id:
        raise PublicAccessError("应用尚未发布", "APPLICATION_NOT_PUBLISHED")
    return access


def issue_visitor_token(application):
    nonce = secrets.token_urlsafe(24)
    payload = {"application_id": application.id, "nonce": nonce}
    return signing.dumps(payload, salt=VISITOR_SALT, compress=True), token_digest(nonce)


def verify_visitor_token(token: str, application):
    max_age = getattr(settings, "APPLICATION_VISITOR_TOKEN_MAX_AGE", 86400)
    try:
        payload = signing.loads(token, salt=VISITOR_SALT, max_age=max_age)
    except signing.BadSignature as exc:
        raise PublicAccessError("访客身份已失效，请刷新页面", "VISITOR_TOKEN_INVALID") from exc
    if payload.get("application_id") != application.id or not payload.get("nonce"):
        raise PublicAccessError("访客身份与当前应用不匹配", "VISITOR_SCOPE_INVALID")
    return token_digest(payload["nonce"])
