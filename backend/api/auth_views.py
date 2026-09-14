import hmac

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import default_token_generator
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core import signing
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.utils.decorators import method_decorator
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import AccountProfile, AccountSession, OrganizationMembership
from .serializers import RegisterSerializer
from .services.account_security import (
    clear_login_failures, ensure_profile, login_is_limited, normalize_email,
    record_login_failure, register_account_session, revoke_account_session,
    revoke_user_sessions, session_digest,
)
from .services.audit import record_audit_event


def ok(data=None, message="success"):
    return Response({"code": 200, "message": message, "data": data})


def error(message, status_code=400, error_code=""):
    return Response({"code": status_code, "message": message,
                     "data": {"error_code": error_code} if error_code else None}, status=status_code)


def _audit(request, user, action, result="SUCCESS", reason_code=""):
    membership = OrganizationMembership.objects.filter(user=user).select_related("organization").first()
    if membership:
        record_audit_event(request, organization=membership.organization, action=action,
                           resource_type="User", resource_id=user.pk, result=result,
                           metadata={"reason_code": reason_code} if reason_code else None)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class CsrfView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return ok({"csrf_token": get_token(request)})


class AuthMeView(APIView):
    def get(self, request):
        profile = ensure_profile(request.user)
        register_account_session(request)
        return ok({
            "id": request.user.id,
            "username": request.user.username,
            "email_masked": _mask_email(profile.normalized_email),
            "email_configured": bool(profile.normalized_email),
            "email_verified": bool(profile.email_verified_at),
        })


def _mask_email(email):
    if not email:
        return ""
    local, _, domain = email.partition("@")
    return f"{local[:2] if len(local) > 2 else local[:1]}***@{domain}"


@method_decorator(csrf_protect, name="dispatch")
class AuthLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        username = str(request.data.get("username", "")).strip()
        if login_is_limited(request, username):
            return error("登录尝试过于频繁，请稍后再试", 429, "LOGIN_RATE_LIMITED")
        user = authenticate(username=username, password=request.data.get("password"))
        if not user:
            record_login_failure(request, username)
            possible = get_user_model().objects.filter(username=username).first()
            if possible:
                _audit(request, possible, "auth.login", "REJECTED", "INVALID_CREDENTIALS")
            return error("用户名或密码错误", 401, "INVALID_CREDENTIALS")
        clear_login_failures(request, username)
        login(request, user)
        request.user = user
        account_session = register_account_session(request)
        _audit(request, user, "auth.login")
        return ok({"id": user.id, "username": user.username,
                   "session_id": str(account_session.public_id)}, "登录成功")


class AuthLogoutView(APIView):
    def post(self, request):
        user = request.user
        key = request.session.session_key or ""
        if key:
            AccountSession.objects.filter(session_key_digest=session_digest(key)).update(revoked_at=timezone.now())
        _audit(request, user, "auth.logout")
        logout(request)
        return ok(True, "已安全退出")


@method_decorator(csrf_protect, name="dispatch")
class AuthRegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        if not settings.ALLOW_USER_REGISTRATION:
            return error("系统当前未开放用户注册，请联系管理员", 403)
        email = normalize_email(request.data.get("email", ""))
        if not email:
            return error("请输入邮箱地址")
        if AccountProfile.objects.filter(normalized_email=email).exists():
            return error("该邮箱已被使用")
        serializer = RegisterSerializer(data={**request.data, "email": email})
        if not serializer.is_valid():
            first = next(iter(serializer.errors.values()))
            return error(str(first[0] if isinstance(first, list) else first))
        try:
            with transaction.atomic():
                user = serializer.save()
                profile = ensure_profile(user)
                profile.normalized_email = email
                profile.save(update_fields=["normalized_email", "updated_at"])
        except IntegrityError:
            return error("用户名或邮箱已被使用")
        login(request, user)
        request.user = user
        register_account_session(request)
        _audit(request, user, "auth.register")
        return ok({"id": user.id, "username": user.username}, "注册成功")


class ChangePasswordView(APIView):
    def post(self, request):
        current = request.data.get("current_password", "")
        password = request.data.get("new_password", "")
        if not request.user.check_password(current):
            return error("当前密码不正确")
        try:
            validate_password(password, request.user)
        except ValidationError as exc:
            return error("；".join(exc.messages))
        request.user.set_password(password)
        request.user.save(update_fields=["password"])
        update_session_auth_hash(request, request.user)
        revoke_user_sessions(request.user, keep_session_key=request.session.session_key or "")
        register_account_session(request)
        _audit(request, request.user, "auth.password.change")
        return ok(True, "密码已修改，其他设备已退出")


@method_decorator(csrf_protect, name="dispatch")
class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = normalize_email(request.data.get("email", ""))
        profile = AccountProfile.objects.select_related("user").filter(normalized_email=email).first()
        if profile and profile.user.is_active:
            uid = urlsafe_base64_encode(force_bytes(profile.user.pk))
            token = default_token_generator.make_token(profile.user)
            url = f"{settings.FRONTEND_BASE_URL}/reset-password?uid={uid}&token={token}"
            try:
                send_mail("知枢密码重置", f"请在有效期内打开以下链接重置密码：\n{url}",
                          settings.DEFAULT_FROM_EMAIL, [profile.normalized_email], fail_silently=False)
            except Exception:
                pass
        return ok(True, "如果该邮箱已注册，你将收到密码重置邮件")


@method_decorator(csrf_protect, name="dispatch")
class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            user_id = force_str(urlsafe_base64_decode(request.data.get("uid", "")))
            user = get_user_model().objects.get(pk=user_id)
        except Exception:
            return error("重置链接无效或已过期", 400, "RESET_TOKEN_INVALID")
        token = str(request.data.get("token", ""))
        if not default_token_generator.check_token(user, token):
            return error("重置链接无效或已过期", 400, "RESET_TOKEN_INVALID")
        password = request.data.get("new_password", "")
        try:
            validate_password(password, user)
        except ValidationError as exc:
            return error("；".join(exc.messages))
        user.set_password(password)
        user.save(update_fields=["password"])
        revoke_user_sessions(user)
        _audit(request, user, "auth.password.reset")
        return ok(True, "密码已重置，请重新登录")


class EmailVerificationRequestView(APIView):
    def post(self, request):
        profile = ensure_profile(request.user)
        if not profile.normalized_email:
            return error("当前账号尚未配置邮箱")
        token = signing.dumps(
            {"uid": request.user.pk, "email": profile.normalized_email},
            salt="zhishu-email-verification",
            compress=True,
        )
        url = f"{settings.FRONTEND_BASE_URL}/verify-email?token={token}"
        try:
            send_mail("验证知枢邮箱", f"请在有效期内打开以下链接验证邮箱：\n{url}",
                      settings.DEFAULT_FROM_EMAIL, [profile.normalized_email], fail_silently=False)
        except Exception:
            return error("验证邮件暂时无法发送，请稍后重试", 503, "EMAIL_DELIVERY_FAILED")
        return ok(True, "验证邮件已发送")


@method_decorator(csrf_protect, name="dispatch")
class EmailVerificationConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            payload = signing.loads(
                str(request.data.get("token", "")), salt="zhishu-email-verification",
                max_age=settings.PASSWORD_RESET_EXPIRE_SECONDS,
            )
            profile = AccountProfile.objects.get(user_id=payload["uid"])
        except Exception:
            return error("验证链接无效或已过期", 400, "EMAIL_TOKEN_INVALID")
        if not hmac.compare_digest(profile.normalized_email or "", str(payload.get("email", ""))):
            return error("验证链接无效或已过期", 400, "EMAIL_TOKEN_INVALID")
        if not profile.email_verified_at:
            profile.email_verified_at = timezone.now()
            profile.save(update_fields=["email_verified_at", "updated_at"])
        return ok(True, "邮箱验证成功")


class AccountSessionListView(APIView):
    def get(self, request):
        current = register_account_session(request)
        live_digests = {
            session_digest(item.session_key)
            for item in Session.objects.filter(expire_date__gt=timezone.now()).only("session_key")
        }
        AccountSession.objects.filter(user=request.user, revoked_at__isnull=True).exclude(
            session_key_digest__in=live_digests
        ).update(revoked_at=timezone.now())
        items = [{
            "id": str(item.public_id), "user_agent": item.user_agent_summary,
            "ip_summary": item.ip_hash[:10] if item.ip_hash else "",
            "created_at": item.created_at, "last_seen_at": item.last_seen_at,
            "current": item.pk == current.pk,
        } for item in AccountSession.objects.filter(user=request.user, revoked_at__isnull=True)]
        return ok({"items": items, "total": len(items)})


class AccountSessionDetailView(APIView):
    def delete(self, request, session_id):
        item = AccountSession.objects.filter(public_id=session_id, user=request.user, revoked_at__isnull=True).first()
        if not item:
            return error("登录会话不存在", 404)
        if hmac.compare_digest(item.session_key_digest, session_digest(request.session.session_key or "")):
            return error("当前会话请使用退出登录", 409)
        revoke_account_session(item)
        _audit(request, request.user, "auth.session.revoke")
        return ok(True, "登录会话已撤销")


class LogoutOtherSessionsView(APIView):
    def post(self, request):
        count = revoke_user_sessions(request.user, keep_session_key=request.session.session_key or "")
        _audit(request, request.user, "auth.session.revoke_others")
        return ok({"revoked_count": count}, "其他设备已退出")
