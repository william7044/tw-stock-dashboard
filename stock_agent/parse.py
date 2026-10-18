"""Small parsing helpers shared by the TWSE, TPEx and TDCC sources."""
import re
from datetime import date

_TAG = re.compile(r"<[^>]+>")
_STOCK_CODE = re.compile(r"[1-9]\d{3}")


def num(value):
    """Parse '1,234.5', '+0.05', '<p>12</p>' into a float; blanks, dashes and 'X' become None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _TAG.sub("", str(value)).replace(",", "").strip()
    if not text or text.strip("-") == "" or text in ("X", "N/A", "NA"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def sign_of(marker):
    """TWSE marks direction as '+', '-' (often wrapped in HTML), ' ' or 'X'."""
    text = _TAG.sub("", str(marker or "")).strip()
    if "-" in text:
        return -1
    if "+" in text:
        return 1
    return 0


def lots(shares):
    """Shares -> 張 (1 張 = 1,000 股), rounded to an int."""
    value = num(shares)
    return None if value is None else int(round(value / 1000))


def r2(value):
    return None if value is None else round(value, 2)


def is_stock(code):
    """Common stocks have 4-digit codes; ETFs (00xx), warrants and bonds are skipped."""
    return bool(_STOCK_CODE.fullmatch(code or ""))


def roc_to_iso(text):
    """'1151016' or '115/10/16' (民國) -> '2026-10-16'."""
    digits = re.sub(r"\D", "", str(text or ""))
    if len(digits) < 7:
        raise ValueError(f"not a ROC date: {text!r}")
    year, month, day = int(digits[:-4]) + 1911, int(digits[-4:-2]), int(digits[-2:])
    return date(year, month, day).isoformat()


def ymd_to_iso(text):
    """'20261016' -> '2026-10-16'."""
    digits = re.sub(r"\D", "", str(text or ""))
    return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8])).isoformat()


def any_date_to_iso(text):
    """Accept either Gregorian (8 digits) or ROC (7 digits / with slashes) dates."""
    digits = re.sub(r"\D", "", str(text or ""))
    return ymd_to_iso(digits) if len(digits) == 8 and digits.startswith("20") else roc_to_iso(digits)


def roc_month_to_iso(text):
    """'11509' -> '2026-09'."""
    digits = re.sub(r"\D", "", str(text or ""))
    return f"{int(digits[:-2]) + 1911:04d}-{int(digits[-2:]):02d}"


def clean(text):
    return str(text or "").replace("　", " ").strip()
