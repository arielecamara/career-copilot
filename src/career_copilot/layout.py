"""Single-column checks and visual line building for text PDFs."""

from __future__ import annotations

import re
from dataclasses import dataclass

from career_copilot.dates import find_date_range
from career_copilot.models import BlockKind, TextBlock
from career_copilot.vocab import looks_like_heading

_BULLET = re.compile(
    r"^(?:[\u2022\u2023\u25cf\u25cb\u25aa\u25e6\u2043\u00b7*]|\d{1,2}[.)]|[\u2013\u2014-])\s+"
)
_NEW_ENTRY = re.compile(
    r"^(?:ph\.?d|m\.?sc|b\.?sc|b\.?a|m\.?a|mba|doctorate|bachelor|master)\b",
    re.IGNORECASE,
)
_ABBREVIATIONS = ("e.g.", "i.e.", "dr.", "mr.", "mrs.", "ms.", "prof.", "sr.", "jr.", "ph.d.", "u.s.")


@dataclass
class VisualLine:
    text: str
    x0: float
    x1: float
    top: float


def is_multi_column(words: list[object], page_width: float) -> bool:
    """True when a vertical gutter splits the page into two prose columns.

    A right-aligned page number or a short table cell is not a second column.
    Both sides of the gutter have to be wide enough to hold prose, on most lines,
    in the same horizontal place.
    """

    if page_width <= 0 or len(words) < 40:
        return False
    lines = cluster_lines(words)
    content_lines = [line for line in lines if len(line) >= 2]
    if len(content_lines) < 8:
        return False
    centers: list[float] = []
    minimum_gap = page_width * 0.12
    minimum_side = page_width * 0.15
    for line in content_lines:
        ordered = sorted(line, key=_x0)
        for index, (left, right) in enumerate(zip(ordered, ordered[1:])):
            gap = _x0(right) - _x1(left)
            if gap < minimum_gap:
                continue
            center = (_x1(left) + _x0(right)) / 2
            if not page_width * 0.30 <= center <= page_width * 0.70:
                continue
            left_words = ordered[: index + 1]
            right_words = ordered[index + 1 :]
            # A page number or one table cell sits on the right. A prose column does not.
            if len(right_words) < 3:
                continue
            if _span(left_words) < minimum_side or _span(right_words) < minimum_side:
                continue
            centers.append(center)
            break
    if len(centers) < len(content_lines) * 0.45:
        return False
    centers.sort()
    median = centers[len(centers) // 2]
    tolerance = page_width * 0.06
    consistent = [center for center in centers if abs(center - median) <= tolerance]
    return len(consistent) >= len(content_lines) * 0.45


def words_to_visual_lines(words: list[object]) -> list[VisualLine]:
    lines: list[VisualLine] = []
    for cluster in cluster_lines(words):
        text = _join_words(cluster).strip()
        if not text:
            continue
        lines.append(
            VisualLine(
                text=text,
                x0=min(_x0(word) for word in cluster),
                x1=max(_x1(word) for word in cluster),
                top=min(_top(word) for word in cluster),
            )
        )
    return lines


def cluster_lines(words: list[object], tolerance: float = 3.0) -> list[list[object]]:
    ordered = sorted(words, key=lambda word: (_top(word), _x0(word)))
    lines: list[list[object]] = []
    for word in ordered:
        if not str(_text(word)).strip():
            continue
        if not lines or abs(_top(word) - _top(lines[-1][0])) > tolerance:
            lines.append([word])
        else:
            lines[-1].append(word)
    for line in lines:
        line.sort(key=_x0)
    return lines


def merge_wrapped_lines(lines: list[VisualLine], content_right: float) -> list[TextBlock]:
    """Join visual wraps. A new heading, bullet, role, or degree stays on its own line."""

    merged: list[VisualLine] = []
    for line in lines:
        if merged and _continues(merged[-1], line, content_right):
            previous = merged[-1]
            text = _join_wrap(previous, line, content_right)
            merged[-1] = VisualLine(
                text=text,
                x0=min(previous.x0, line.x0),
                x1=max(previous.x1, line.x1),
                top=previous.top,
            )
        else:
            merged.append(line)
    blocks: list[TextBlock] = []
    for line in merged:
        block = _block_from_line(line.text)
        if block is not None:
            blocks.append(block)
    return blocks


def is_bullet_text(text: str) -> bool:
    return _BULLET.match(text) is not None


def strip_bullet(text: str) -> str:
    return _BULLET.sub("", text, count=1).strip()


def _continues(previous: VisualLine, nxt: VisualLine, content_right: float) -> bool:
    if looks_like_heading(previous.text) or looks_like_heading(nxt.text):
        return False
    if _BULLET.match(nxt.text):
        return False
    if find_date_range(nxt.text) is not None:
        return False
    if "|" in nxt.text:
        return False
    if _NEW_ENTRY.match(nxt.text):
        return False
    # A line that reaches the right margin is wrapped, even when the break
    # falls on a period. A short line that ends a sentence starts a new block.
    if previous.x1 >= content_right - 36:
        return True
    if _ends_sentence(previous.text):
        return False
    if previous.text.endswith((",", "(", "/", ":", ";")):
        return True
    if nxt.text[:1].islower() or nxt.text[:1] in "([":
        return True
    return False


def _join_wrap(previous: VisualLine, nxt: VisualLine, content_right: float) -> str:
    # A hyphen at the right margin is a line-break hyphen, not a compound kept in the source.
    if (
        previous.text.endswith("-")
        and nxt.text[:1].islower()
        and previous.x1 >= content_right - 36
    ):
        return previous.text[:-1] + nxt.text
    return previous.text.rstrip() + " " + nxt.text.lstrip()


def _ends_sentence(text: str) -> bool:
    stripped = text.rstrip()
    if not stripped.endswith((".", "!", "?")):
        return False
    return not stripped.casefold().endswith(_ABBREVIATIONS)


def _block_from_line(text: str) -> TextBlock | None:
    stripped = text.strip()
    if not stripped:
        return None
    if _BULLET.match(stripped):
        body = strip_bullet(stripped)
        if not body:
            return None
        return TextBlock(text=body, kind=BlockKind.bullet)
    if _is_heading_text(stripped):
        return TextBlock(text=stripped, kind=BlockKind.heading)
    return TextBlock(text=stripped, kind=BlockKind.paragraph)


def _is_heading_text(text: str) -> bool:
    if find_date_range(text) is not None or "|" in text:
        return False
    return looks_like_heading(text)


def _join_words(words: list[object]) -> str:
    parts: list[str] = []
    previous_x1: float | None = None
    for word in words:
        text = str(_text(word)).strip()
        if not text:
            continue
        if previous_x1 is not None and _x0(word) - previous_x1 > 1.5:
            parts.append(" ")
        parts.append(text)
        previous_x1 = _x1(word)
    return "".join(parts)


def _field(word: object, name: str) -> object:
    if isinstance(word, dict):
        return word[name]
    return getattr(word, name)


def _span(words: list[object]) -> float:
    if not words:
        return 0.0
    return _x1(words[-1]) - _x0(words[0])


def _x0(word: object) -> float:
    return float(_field(word, "x0"))


def _x1(word: object) -> float:
    return float(_field(word, "x1"))


def _top(word: object) -> float:
    return float(_field(word, "top"))


def _text(word: object) -> object:
    return _field(word, "text")
