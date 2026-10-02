"""Shared numeric revision entry; stored legacy revisions are not rewritten."""
import re

_REVISION_NUMBER = re.compile(r"(?:REV\.\s*)?([0-9]+)", re.IGNORECASE)


def normalize_revision_code(value: str) -> str:
    match = _REVISION_NUMBER.fullmatch(str(value or "").strip())
    if not match:
        raise ValueError("개정번호는 숫자만 입력해 주세요. REV.는 자동으로 붙습니다.")
    return "REV." + match.group(1)


def revision_key(value: str) -> str:
    """Compare numeric legacy spellings without changing their stored values."""
    raw = str(value or "").strip()
    match = _REVISION_NUMBER.fullmatch(raw)
    return ("number:" + (match.group(1).lstrip("0") or "0")) if match else "legacy:" + raw.casefold()
