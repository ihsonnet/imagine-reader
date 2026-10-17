"""Site-wide daily limit on paid AI requests.

Every paid request is checked against today's total before it is sent and
recorded after it succeeds. Requests already on their way are reserved, so
pictures and narration prepared side by side cannot overshoot the limit
together. Costs are the providers' estimates; the limit is meant to stop
runaway use, not to reconcile an invoice.
"""

import threading
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Settings
from .db import Spend, utcnow

_lock = threading.Lock()
_reserved = 0.0  # estimated cost of requests sent by this process and not yet recorded


def today() -> str:
    return utcnow().date().isoformat()


def spent_today(db: Session) -> float:
    return db.scalar(select(func.coalesce(func.sum(Spend.usd), 0.0)).where(Spend.day == today())) or 0.0


def affordable(db: Session, settings: Settings, cost: float) -> bool:
    return cost <= 0 or spent_today(db) + _reserved + cost <= settings.daily_spend_limit_usd


def reserve(db: Session, settings: Settings, cost: float) -> bool:
    """Hold ``cost`` against today's limit if it fits. Release it with ``release`` once recorded or failed."""
    global _reserved
    with _lock:
        if not affordable(db, settings, cost):
            return False
        _reserved += max(cost, 0.0)
        return True


def release(cost: float) -> None:
    global _reserved
    with _lock:
        _reserved = max(0.0, _reserved - max(cost, 0.0))


@contextmanager
def reserved(cost: float) -> Iterator[None]:
    """Release a reservation when the request is over, whatever happened."""
    try:
        yield
    finally:
        release(cost)


def provider_name(provider: object) -> str | None:
    """The company a provider's requests go to: its route (OpenRouter's providers say so), else from its id:
    "gemini-…" is gemini, "cloudflare-…" cloudflare."""
    model = getattr(provider, "reader", None) or provider  # a story brain's own id names its plan, not its model
    return getattr(model, "route", None) or (getattr(model, "id", None) or "").split("-", 1)[0] or None


def record(db: Session, kind: str, book_id: str, cost: float, provider: object = None) -> None:
    """Record a paid request, with the company it went to (the provider, or a story brain and its model)."""
    if cost > 0:
        db.add(Spend(day=today(), kind=kind, book_id=book_id, usd=cost, provider=provider_name(provider)))


KINDS = ("speech", "image", "text")  # the voice, the pictures, and the language model reading and planning the story


def _costs(by_kind: dict[str, float]) -> dict[str, float]:
    costs = {kind: round(by_kind.get(kind, 0.0), 4) for kind in KINDS}
    return {**costs, "usd": round(sum(by_kind.values()), 4)}


def summary(db: Session, days: int = 30) -> dict:
    """What this site's AI requests cost: over recent periods, day by day, book by book (deleted ones too), and
    company by company."""
    rows = db.execute(
        select(Spend.day, Spend.book_id, Spend.kind, Spend.provider, func.sum(Spend.usd)).group_by(
            Spend.day, Spend.book_id, Spend.kind, Spend.provider
        )
    ).all()
    now = date.fromisoformat(today())
    first = {span: (now - timedelta(days=span - 1)).isoformat() for span in (1, 7, days)}
    spans = (*first, 0)  # 0: all time
    periods = {span: defaultdict(float) for span in spans}
    by_day = {(now - timedelta(days=back)).isoformat(): defaultdict(float) for back in range(days - 1, -1, -1)}
    by_book: dict[str, dict] = {}
    by_provider: dict[str, dict] = {}
    for day, book_id, kind, provider, usd in rows:
        company = by_provider.setdefault(
            provider or "", {"periods": {span: 0.0 for span in spans}, "kinds": defaultdict(float)}
        )
        for span in spans:
            if span == 0 or day >= first[span]:
                periods[span][kind] += usd
                company["periods"][span] += usd
        company["kinds"][kind] += usd
        if day in by_day:
            by_day[day][kind] += usd
        book = by_book.setdefault(book_id, {"kinds": defaultdict(float), "first_day": day, "last_day": day})
        book["kinds"][kind] += usd
        book["first_day"], book["last_day"] = min(book["first_day"], day), max(book["last_day"], day)
    books = [
        {"book_id": book_id, **_costs(book["kinds"]), "first_day": book["first_day"], "last_day": book["last_day"]}
        for book_id, book in by_book.items()
    ]
    providers = [
        {
            "provider": name or None,  # None: requests made before the provider was recorded, that could not be told
            "periods": [{"days": span, "usd": round(usd, 4)} for span, usd in company["periods"].items()],
            **_costs(company["kinds"]),
        }
        for name, company in by_provider.items()
    ]
    return {
        "today": today(),
        "periods": [{"days": span, **_costs(kinds)} for span, kinds in periods.items()],
        "days": [{"day": day, **_costs(kinds)} for day, kinds in by_day.items()],
        "books": sorted(books, key=lambda book: book["usd"], reverse=True),
        "providers": sorted(providers, key=lambda company: company["usd"], reverse=True),
    }
