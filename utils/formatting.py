"""Small formatting / parsing helpers shared across handlers and the scheduler."""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

from dateutil.parser import parse
from dateutil.tz import gettz

from config import config

_MONTHS_GEN = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
_MONTHS_NOM = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]

_DAYS = [
    "понедельник", "вторник", "среда", "четверг",
    "пятница", "суббота", "воскресенье",
]


def tz() -> object:
    """The app's IANA timezone as a tzinfo, used for 'today' calculations."""
    return gettz(config.scheduler_timezone) or gettz("Europe/Moscow")


def today() -> date:
    """Today's date in the application timezone."""
    return datetime.now(tz()).date()


def parse_date(text: str) -> date | None:
    """Best-effort parse of a free-form date string into a :class:`date`.

    Accepts ``ГГГГ-ММ-ДД``, ``ДД.ММ.ГГГГ``, ``ДД.ММ``, Russian month names and
    any format ``python-dateutil`` understands. When the year is omitted it is
    inferred to be the *current* year, or the next year if the month/day has
    already passed this year. Returns ``None`` when the text cannot be interpreted
    as a sensible date.
    """
    if not text:
        return None
    cleaned = text.strip()

    # Fast path: ISO / dot / slash separated dates.
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue

    # Day + month without a year, e.g. "25.03" or "25/03".
    for fmt in ("%d.%m", "%d/%m"):
        try:
            d = datetime.strptime(cleaned, fmt).date()
            return _infer_year(d)
        except ValueError:
            continue

    # Russian textual dates like "25 декабря 2024" or "двадцать пятого декабря".
    lowered = cleaned.lower()
    for i, month in enumerate(_MONTHS_GEN, start=1):
        if month in lowered:
            lowered = lowered.replace(month, str(i))

    try:
        parsed = parse(lowered, dayfirst=True, tzinfos=tz()).date()
    except (ValueError, TypeError):
        return None

    # If no year was present, dateutil defaults to 1900; infer a sensible one.
    if parsed.year == 1900:
        return _infer_year(date(parsed.year, parsed.month, parsed.day))
    if parsed.year < 1900:
        return None
    return parsed


def _infer_year(d: date) -> date:
    """If the date has already passed this year, roll it to next year."""
    this_year = today().year
    candidate = date(this_year, d.month, d.day)
    if candidate >= today():
        return candidate
    return candidate.replace(year=this_year + 1)


def format_date(d: date) -> str:
    """Pretty date, e.g. ``17 сентября 2026``."""
    return f"{d.day} {_MONTHS_GEN[d.month - 1]} {d.year}"


def format_date_short(d: date) -> str:
    """Short date without the year, e.g. ``17 сентября``."""
    return f"{d.day} {_MONTHS_GEN[d.month - 1]}"


def humanize_diff(days: int) -> str:
    """Turn an integer number of days into a natural Russian phrase.

    >>> humanize_diff(-1)   # yesterday event
    'вчера'
    >>> humanize_diff(0)
    'сегодня'
    >>> humanize_diff(3)
    'через 3 дня'
    """
    if days == 0:
        return "сегодня"
    if days == 1:
        return "завтра"
    if days == -1:
        return "вчера"
    if days > 1:
        return f"через {days} дня" if 1 < days < 5 else f"через {days} дней"
    return f"{abs(days)} дня назад" if abs(days) < 5 else f"{abs(days)} дней назад"


def weekday_name(d: date) -> str:
    """Russian weekday name for the given date, e.g. ``четверг``."""
    return _DAYS[d.weekday()]


def join_lines(items: Iterable[str]) -> str:
    """Join lines for message output, dropping empty entries."""
    return "\n".join(x for x in items if x)


def month_name(index: int) -> str:
    return _MONTHS_NOM[index - 1]
