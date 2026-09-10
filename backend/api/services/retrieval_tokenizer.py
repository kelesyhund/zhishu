import re


def tokenize_text(text: str) -> list[str]:
    """使用无外部词典的确定性规则切分英文、数字和中文。"""

    normalized = text.lower()
    latin = re.findall(r"[a-z0-9_]+", normalized)
    chinese = re.findall(r"[\u4e00-\u9fff]", normalized)
    chinese_bigrams = ["".join(chinese[index : index + 2]) for index in range(len(chinese) - 1)]
    return latin + chinese + chinese_bigrams
