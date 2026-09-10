from django.conf import settings


class ModelCryptoError(Exception):
    """数据库模型密钥无法被安全加解密。"""


def _fernet():
    key = settings.MODEL_CONFIG_ENCRYPTION_KEY.strip()
    if not key:
        raise ModelCryptoError("服务器尚未配置模型密钥加密能力")
    try:
        from cryptography.fernet import Fernet

        return Fernet(key.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ModelCryptoError("服务器模型密钥配置无效") from exc


def encrypt_api_key(api_key: str) -> str:
    value = api_key.strip()
    if not value:
        raise ModelCryptoError("API Key不能为空")
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_api_key(encrypted_api_key: str) -> str:
    try:
        return _fernet().decrypt(encrypted_api_key.encode("utf-8")).decode("utf-8")
    except ModelCryptoError:
        raise
    except Exception as exc:
        raise ModelCryptoError("服务器无法解密模型密钥") from exc


def mask_api_key(last4: str) -> str:
    return f"••••••••{last4}" if last4 else "••••••••"

