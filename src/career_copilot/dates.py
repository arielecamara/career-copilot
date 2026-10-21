"""Copy date ranges from a line. Unknown days stay unknown. Open roles have no end date."""

from __future__ import annotations

import re
from dataclasses import dataclass

from career_copilot.models import ExactDate

_MONTHS = {
    "january": 1,
    "jan": 1,
    "janeiro": 1,
    "february": 2,
    "feb": 2,
    "fevereiro": 2,
    "fev": 2,
    "march": 3,
    "mar": 3,
    "marco": 3,
    "março": 3,
    "april": 4,
    "apr": 4,
    "abril": 4,
    "abr": 4,
    "may": 5,
    "maio": 5,
    "mai": 5,
    "june": 6,
    "jun": 6,
    "junho": 6,
    "july": 7,
    "jul": 7,
    "julho": 7,
    "august": 8,
    "aug": 8,
    "agosto": 8,
    "ago": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "setembro": 9,
    "set": 9,
    "october": 10,
    "oct": 10,
    "outubro": 10,
    "out": 10,
    "november": 11,
    "nov": 11,
    "novembro": 11,
    "december": 12,
    "dec": 12,
    "dezembro": 12,
    "dez": 12,
}

_ONGOING = {
    "present",
    "current",
    "now",
    "ongoing",
    "today",
    "atual",
    "atualidade",
    "presente",
    "hoje",
    "corrente",
}

# Longer month names are listed first so "september" wins over "sep".
_MONTH_PATTERN = (
    r"janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro|"
    r"january|february|september|october|november|december|august|march|april|"
    r"sept|june|july|"
    r"jan|fev|feb|mar|abr|apr|mai|may|jun|jul|ago|aug|set|sep|out|oct|nov|dez|dec"
)
_DATE_TOKEN = (
    r"(?:\d{1,2}\s+(?:" + _MONTH_PATTERN + r")\.?\s+\d{4}"
    r"|(?:" + _MONTH_PATTERN + r")\.?\s+\d{1,2},\s+\d{4}"
    r"|(?:" + _MONTH_PATTERN + r")\.?\s+\d{4}"
    r"|\d{1,2}[/-]\d{4}"
    r"|\d{4})"
)
_ONGOING_TOKEN = (
    r"present|current|now|ongoing|today|atualidade|presente|atual|hoje|corrente"
)
_RANGE = re.compile(
    r"(?P<span>"
    + _DATE_TOKEN
    + r"\s*(?:–|—|-|to|até|ate)\s*(?:"
    + _DATE_TOKEN
    + r"|"
    + _ONGOING_TOKEN
    + r"))",
    re.IGNORECASE,
)
_SEPARATOR = re.compile(r"\s*(?:–|—|-|to|até|ate)\s*", re.IGNORECASE)


@dataclass(frozen=True)
class DateRange:
    start: ExactDate
    end: ExactDate | None
    span_raw: str
    start_index: int
    end_index: int


def find_date_range(text: str) -> DateRange | None:
    """Return the last valid range on the line. Role dates usually sit at the end."""

    found: DateRange | None = None
    for match in _RANGE.finditer(text):
        parsed = _parse_span(match.group("span"))
        if parsed is None:
            continue
        start, end = parsed
        found = DateRange(
            start=start,
            end=end,
            span_raw=match.group("span"),
            start_index=match.start(),
            end_index=match.end(),
        )
    return found


def is_ongoing(token: str) -> bool:
    return token.strip().casefold().rstrip(".") in _ONGOING


def parse_date_token(token: str) -> ExactDate | None:
    """Parse one date token. Returns None for an open-ended word or an invalid token."""

    raw = token.strip()
    if is_ongoing(raw):
        return None
    return _parse_closed(raw)


def _parse_span(span: str) -> tuple[ExactDate, ExactDate | None] | None:
    parts = _SEPARATOR.split(span, maxsplit=1)
    if len(parts) != 2:
        return None
    start = _parse_closed(parts[0].strip())
    if start is None:
        return None
    end_token = parts[1].strip()
    if end_token.casefold().rstrip(".") in _ONGOING:
        return start, None
    end = _parse_closed(end_token)
    if end is None:
        return None
    if _order(end) < _order(start):
        return None
    return start, end


def _parse_closed(token: str) -> ExactDate | None:
    raw = token.strip().rstrip(".")
    if not raw:
        return None

    numeric = re.fullmatch(r"(\d{1,2})[/-](\d{4})", raw)
    if numeric:
        month = int(numeric.group(1))
        year = int(numeric.group(2))
        if not 1 <= month <= 12:
            return None
        return _exact(token.strip(), year, month)

    day_month = re.fullmatch(
        r"(\d{1,2})\s+(" + _MONTH_PATTERN + r")\.?\s+(\d{4})",
        raw,
        re.IGNORECASE,
    )
    if day_month:
        day = int(day_month.group(1))
        month = _month_number(day_month.group(2))
        year = int(day_month.group(3))
        if month is None or not 1 <= day <= 31:
            return None
        return _exact(token.strip(), year, month, day)

    month_day = re.fullmatch(
        r"(" + _MONTH_PATTERN + r")\.?\s+(\d{1,2}),\s+(\d{4})",
        raw,
        re.IGNORECASE,
    )
    if month_day:
        month = _month_number(month_day.group(1))
        day = int(month_day.group(2))
        year = int(month_day.group(3))
        if month is None or not 1 <= day <= 31:
            return None
        return _exact(token.strip(), year, month, day)

    month_year = re.fullmatch(
        r"(" + _MONTH_PATTERN + r")\.?\s+(\d{4})",
        raw,
        re.IGNORECASE,
    )
    if month_year:
        month = _month_number(month_year.group(1))
        year = int(month_year.group(2))
        if month is None:
            return None
        return _exact(token.strip(), year, month)

    year_only = re.fullmatch(r"(\d{4})", raw)
    if year_only:
        return _exact(token.strip(), int(year_only.group(1)))
    return None


def _exact(raw: str, year: int, month: int | None = None, day: int | None = None) -> ExactDate | None:
    """Return None for years outside a career-date range, such as 3000–2500 BCE."""

    if not 1900 <= year <= 2100:
        return None
    return ExactDate(raw=raw, year=year, month=month, day=day)


def _month_number(name: str) -> int | None:
    return _MONTHS.get(name.casefold().rstrip("."))


def _order(value: ExactDate) -> tuple[int, int, int]:
    return (value.year, value.month or 1, value.day or 1)
