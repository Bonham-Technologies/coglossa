"""Cheap, offline text measurements. Deliberately conservative: no tokenizer downloads."""

import math
import re

# Character ranges of scripts written without spaces between words (bounds as \uXXXX escapes).
SPACELESS_RANGES = (
    r"\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"  # Japanese kana, CJK
    r"\u0e00-\u0e7f\u0e80-\u0eff\u1000-\u109f\u1780-\u17ff"  # Thai, Lao, Myanmar, Khmer
)
SPACELESS = re.compile(f"[{SPACELESS_RANGES}]")


def count_words(text: str) -> int:
    spaceless = len(SPACELESS.findall(text))
    return len(SPACELESS.sub(" ", text).split()) + math.ceil(spaceless / 2)


def estimate_tokens(text: str, bytes_per_token: float = 2.5) -> int:
    return math.ceil(len(text.encode("utf-8")) / bytes_per_token)
