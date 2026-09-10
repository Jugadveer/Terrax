"""
Number and date presentation helpers.

Prices are stored in rupees. Indian readers expect lakh and crore groupings,
so `money` produces those rather than the k/M/B scale, and `money_exact`
produces the 2-2-3 digit grouping (45,00,000) used on invoices and deeds.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone

CRORE = Decimal("10000000")
LAKH = Decimal("100000")

# Indicative only. A real deployment would read this from a rates feed; it is
# fixed here so that a demo without network access still renders both currencies.
USD_PER_INR = Decimal("0.0114")


def money(value: Decimal | int | float | None, currency: str = "INR") -> str:
    """Compact price: ``2.45 Cr``, ``85.0 L``, ``$28.5k``."""
    if value is None:
        return "-"
    amount = Decimal(str(value))
    if currency == "USD":
        return _money_usd(amount * USD_PER_INR)
    if amount >= CRORE:
        return f"₹{_trim(amount / CRORE)} Cr"
    if amount >= LAKH:
        return f"₹{_trim(amount / LAKH)} L"
    return f"₹{money_exact(amount)}"


def _money_usd(amount: Decimal) -> str:
    if amount >= 1_000_000:
        return f"${_trim(amount / Decimal(1_000_000))}M"
    if amount >= 1_000:
        return f"${_trim(amount / Decimal(1_000))}k"
    return f"${amount:,.0f}"


def money_exact(value: Decimal | int | float | None) -> str:
    """Full rupee value with Indian digit grouping: ``1,20,45,000``."""
    if value is None:
        return "-"
    digits = f"{int(Decimal(str(value))):d}"
    negative, digits = digits.startswith("-"), digits.lstrip("-")
    if len(digits) <= 3:
        grouped = digits
    else:
        head, tail = digits[:-3], digits[-3:]
        pairs = []
        while len(head) > 2:
            pairs.insert(0, head[-2:])
            head = head[:-2]
        if head:
            pairs.insert(0, head)
        grouped = ",".join(pairs) + "," + tail
    return ("-" if negative else "") + grouped


def _trim(value: Decimal) -> str:
    """Two significant decimals, with trailing zeroes removed."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def compact(value: int | float | None) -> str:
    """Counts and areas: ``1.2k``, ``18.4k``, ``3.1M``."""
    if value is None:
        return "-"
    number = float(value)
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if abs(number) >= limit:
            return f"{number / limit:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{number:,.0f}"


def percent(value: float | Decimal | None, signed: bool = False) -> str:
    """``12.4%`` or ``+12.4%``."""
    if value is None:
        return "-"
    number = float(value)
    sign = "+" if signed and number > 0 else ""
    return f"{sign}{number:.1f}%"


def relative_time(value: datetime | None) -> str:
    """``just now``, ``14m ago``, ``3d ago``, ``12 Mar``."""
    if value is None:
        return "-"
    now = timezone.now()
    delta = now - value
    if delta < timedelta(minutes=1):
        return "just now"
    if delta < timedelta(hours=1):
        return f"{int(delta.total_seconds() // 60)}m ago"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)}h ago"
    if delta < timedelta(days=7):
        return f"{delta.days}d ago"
    if value.year == now.year:
        return value.strftime("%-d %b") if _supports_dash() else value.strftime("%d %b")
    return value.strftime("%b %Y")


def _supports_dash() -> bool:
    """``%-d`` is glibc only; Windows needs ``%d``."""
    try:
        datetime(2026, 1, 5).strftime("%-d")
        return True
    except ValueError:
        return False
