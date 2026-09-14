import hashlib
import hmac

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.utils import timezone

from api.models import AccountProfile, AccountSession


def normalize_email(value: str) -> str:
    return str(value or "").strip().lower()


def session_digest(session_key: str) -> str:
    return hashlib.sha256(session_key.encode("utf-8")).hexdigest()


def _request_ip_hash(request) -> str:
    value = str(request.META.get("REMOTE_ADDR", "")).strip()
    if not value:
        return ""
    key = str(settings.AUDIT_IP_HASH_KEY).encode("utf-8")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()


def ensure_profile(user) -> AccountProfile:
    return AccountProfile.objects.get_or_create(user=user)[0]


def register_account_session(request) -> AccountSession:
    if not request.session.session_key:
        request.session.save()
    return AccountSession.objects.update_or_create(
        session_key_digest=session_digest(request.session.session_key),
        defaults={
            "user": request.user,
            "user_agent_summary": str(request.META.get("HTTP_USER_AGENT", ""))[:200],
            "ip_hash": _request_ip_hash(request),
            "revoked_at": None,
        },
    )[0]


def revoke_account_session(account_session: AccountSession) -> None:
    if account_session.revoked_at:
        return
    for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        if hmac.compare_digest(session_digest(session.session_key), account_session.session_key_digest):
            session.delete()
            break
    account_session.revoked_at = timezone.now()
    account_session.save(update_fields=["revoked_at"])


def revoke_user_sessions(user, *, keep_session_key: str = "") -> int:
    keep_digest = session_digest(keep_session_key) if keep_session_key else ""
    count = 0
    for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        try:
            belongs_to_user = str(session.get_decoded().get("_auth_user_id", "")) == str(user.pk)
        except Exception:
            belongs_to_user = False
        digest = session_digest(session.session_key)
        if not belongs_to_user or (keep_digest and hmac.compare_digest(digest, keep_digest)):
            continue
        session.delete()
        count += 1
    queryset = AccountSession.objects.filter(user=user, revoked_at__isnull=True)
    if keep_digest:
        queryset = queryset.exclude(session_key_digest=keep_digest)
    queryset.update(revoked_at=timezone.now())
    return count


def login_rate_key(request, username: str) -> tuple[str, str]:
    user_hash = hashlib.sha256(normalize_email(username).encode("utf-8")).hexdigest()[:24]
    ip_hash = _request_ip_hash(request)[:24] or "unknown"
    return f"login:user:{user_hash}", f"login:ip:{ip_hash}"


def login_is_limited(request, username: str) -> bool:
    limit = settings.LOGIN_RATE_LIMIT_ATTEMPTS
    return any(int(cache.get(key, 0)) >= limit for key in login_rate_key(request, username))


def record_login_failure(request, username: str) -> None:
    window = settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
    for key in login_rate_key(request, username):
        if cache.add(key, 1, timeout=window):
            continue
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=window)


def clear_login_failures(request, username: str) -> None:
    cache.delete_many(login_rate_key(request, username))
