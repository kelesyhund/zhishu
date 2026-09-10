import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings


class EndpointValidationError(Exception):
    """模型地址不满足安全规则。"""


def _private_endpoints_allowed() -> bool:
    return bool(settings.DEBUG and settings.ALLOW_PRIVATE_MODEL_ENDPOINTS)


def _validate_ip(value: str) -> None:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise EndpointValidationError("模型地址解析结果无效") from exc
    if not address.is_global and not _private_endpoints_allowed():
        raise EndpointValidationError("模型地址不能指向本机、内网或保留网络")


def normalize_base_url(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise EndpointValidationError("模型地址不能为空")
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise EndpointValidationError("模型地址只支持HTTP或HTTPS")
    if parsed.username or parsed.password:
        raise EndpointValidationError("模型地址不能包含用户名或密码")
    if parsed.query or parsed.fragment:
        raise EndpointValidationError("模型地址不能包含查询参数或片段")
    if not parsed.hostname:
        raise EndpointValidationError("模型地址缺少有效域名")
    if parsed.scheme == "http" and not _private_endpoints_allowed():
        raise EndpointValidationError("模型地址必须使用HTTPS")
    if parsed.hostname.lower() == "localhost" or parsed.hostname.lower().endswith(".localhost"):
        if not _private_endpoints_allowed():
            raise EndpointValidationError("模型地址不能指向本机、内网或保留网络")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


def validate_model_endpoint(value: str) -> str:
    normalized = normalize_base_url(value)
    parsed = urlsplit(normalized)
    hostname = parsed.hostname or ""
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        try:
            results = socket.getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise EndpointValidationError("无法解析模型服务地址") from exc
        addresses = {item[4][0] for item in results}
        if not addresses:
            raise EndpointValidationError("无法解析模型服务地址")
        for address in addresses:
            _validate_ip(address)
    else:
        _validate_ip(hostname)
    return normalized

