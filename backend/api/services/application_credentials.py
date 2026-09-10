import hashlib
import hmac
import secrets

from django.utils import timezone

from api.models import ApplicationCredential


class ApplicationCredentialError(Exception):
    def __init__(self, message="访问凭证无效或已失效", error_code="INVALID_CREDENTIAL"):
        super().__init__(message)
        self.message = message
        self.error_code = error_code


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_application_credential(application, name, expires_at=None):
    prefix = secrets.token_hex(6)
    token = f"kc_app_{prefix}_{secrets.token_urlsafe(32)}"
    credential = ApplicationCredential.objects.create(
        application=application,
        name=name.strip(),
        key_prefix=prefix,
        secret_digest=_digest(token),
        last4=token[-4:],
        expires_at=expires_at,
    )
    return credential, token


def authenticate_application_credential(token: str, application_id=None):
    parts = token.split("_", 3)
    if len(parts) != 4 or parts[:2] != ["kc", "app"]:
        raise ApplicationCredentialError()
    credential = ApplicationCredential.objects.select_related(
        "application__current_published_version"
    ).filter(key_prefix=parts[2]).first()
    if not credential or not hmac.compare_digest(credential.secret_digest, _digest(token)):
        raise ApplicationCredentialError()
    if application_id is not None and credential.application_id != application_id:
        raise ApplicationCredentialError()
    if not credential.enabled:
        raise ApplicationCredentialError("访问凭证已停用", "CREDENTIAL_DISABLED")
    if credential.expires_at and credential.expires_at <= timezone.now():
        raise ApplicationCredentialError("访问凭证已过期", "CREDENTIAL_EXPIRED")
    credential.last_used_at = timezone.now()
    credential.save(update_fields=["last_used_at"])
    return credential
