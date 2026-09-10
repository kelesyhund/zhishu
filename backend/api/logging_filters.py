import logging
import re


PUBLIC_TOKEN_PATTERN = re.compile(r"kc_pub_[A-Fa-f0-9]{12}_[A-Za-z0-9_-]{16,}")
APPLICATION_KEY_PATTERN = re.compile(r"kc_app_[A-Fa-f0-9]{12}_[A-Za-z0-9_-]{16,}")


def redact_application_secrets(value):
    if isinstance(value, str):
        value = PUBLIC_TOKEN_PATTERN.sub("kc_pub_<redacted>", value)
        return APPLICATION_KEY_PATTERN.sub("kc_app_<redacted>", value)
    if isinstance(value, tuple):
        return tuple(redact_application_secrets(item) for item in value)
    if isinstance(value, list):
        return [redact_application_secrets(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_application_secrets(item) for key, item in value.items()}
    return value


class ApplicationSecretFilter(logging.Filter):
    def filter(self, record):
        record.msg = redact_application_secrets(record.msg)
        record.args = redact_application_secrets(record.args)
        return True
