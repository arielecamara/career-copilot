"""Daily spend ledger. A call is refused before it can pass 10 percent of the quota."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

# Public list prices used only to refuse a call before it is sent.
# Score uses the cheaper model. A draft uses the stronger one.
_PRICE_PER_TOKEN = {
    "score": (1.25 / 1_000_000, 2.50 / 1_000_000, 1200),
    "draft": (2.0 / 1_000_000, 6.0 / 1_000_000, 2000),
}
DAILY_SHARE = 0.10
OFF_NOTE = "Model is off. No call was made."
NO_KEY_NOTE = "Model is off. No API key is set. No call was made."
NO_QUOTA_NOTE = "Model is off. No quota is set. No call was made."
DAILY_LIMIT_NOTE = "Stopped. Reached the daily limit."


def model_enabled() -> bool:
    return os.environ.get("CAREER_COPILOT_MODEL", "").strip().lower() == "on"


def api_key() -> str:
    return os.environ.get("XAI_API_KEY", "").strip()


def quota_usd() -> float:
    """The quota the candidate already has. The daily cap is 10 percent of it."""

    raw = os.environ.get("CAREER_COPILOT_QUOTA_USD", "").strip()
    try:
        value = float(raw)
    except ValueError:
        return 0.0
    if value <= 0 or value > 1_000_000:
        return 0.0
    return value


def daily_cap_usd() -> float:
    return round(quota_usd() * DAILY_SHARE, 6)


def ledger_path() -> Path:
    override = os.environ.get("CAREER_COPILOT_LEDGER", "").strip()
    if override:
        return Path(override)
    return Path.cwd() / ".career-copilot" / "spend-ledger.json"


def worst_case_usd(kind: str, input_chars: int) -> tuple[float, int]:
    """Return the worst-case dollar cost and the output-token cap for one call."""

    price_in, price_out, max_out = _PRICE_PER_TOKEN[kind]
    incoming = max(1, input_chars // 4)
    return incoming * price_in + max_out * price_out, max_out


def spent_today(path: Path | None = None, *, today: date | None = None) -> float:
    day, spent = _read(path or ledger_path(), today or date.today())
    if day != (today or date.today()).isoformat():
        return 0.0
    return spent


def gate(kind: str, input_chars: int, *, path: Path | None = None, today: date | None = None) -> tuple[bool, str, float, int]:
    """Decide whether one call may be sent. This function does not reserve money."""

    estimate, max_out = worst_case_usd(kind, input_chars)
    if not model_enabled():
        return False, OFF_NOTE, estimate, max_out
    if not api_key():
        return False, NO_KEY_NOTE, estimate, max_out
    cap = daily_cap_usd()
    if cap <= 0:
        return False, NO_QUOTA_NOTE, estimate, max_out
    spent = spent_today(path, today=today)
    if spent + estimate > cap:
        return False, DAILY_LIMIT_NOTE, estimate, max_out
    return True, "", estimate, max_out


def reserve(amount: float, *, path: Path | None = None, today: date | None = None) -> bool:
    """Hold the worst case before a call. False means the hold would pass the cap."""

    if amount < 0:
        return False
    target = path or ledger_path()
    day = (today or date.today()).isoformat()
    current_day, spent = _read(target, today or date.today())
    if current_day != day:
        spent = 0.0
    cap = daily_cap_usd()
    if cap <= 0 or spent + amount > cap:
        return False
    _write(target, day, spent + amount)
    return True


def settle(reserved: float, actual: float | None, *, path: Path | None = None, today: date | None = None) -> None:
    """Replace a hold with the real cost. A failed call with no cost releases the hold."""

    target = path or ledger_path()
    day = (today or date.today()).isoformat()
    current_day, spent = _read(target, today or date.today())
    if current_day != day:
        spent = 0.0
    spent = max(0.0, spent - reserved)
    if actual is not None and actual > 0:
        spent += actual
    _write(target, day, spent)


def _read(path: Path, today: date) -> tuple[str, float]:
    if not path.is_file():
        return today.isoformat(), 0.0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return today.isoformat(), 0.0
    day = raw.get("day")
    spent = raw.get("usd")
    if not isinstance(day, str) or not isinstance(spent, (int, float)) or spent < 0:
        return today.isoformat(), 0.0
    return day, float(spent)


def _write(path: Path, day: str, spent: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"day": day, "usd": round(spent, 6)}) + "\n"
    path.write_text(payload, encoding="utf-8")
