"""Standalone generator for a public repo containing official dates only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


SOURCES = {
    "FOMC": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    "CPI": "https://www.bls.gov/schedule/news_release/cpi.htm?force_isolation=true",
    "NFP": "https://www.bls.gov/cps/publications/release-calendar.htm",
}
MONTHS = {}
for index, names in enumerate((
    ("January", "Jan"), ("February", "Feb"), ("March", "Mar"),
    ("April", "Apr"), ("May",), ("June", "Jun"), ("July", "Jul"),
    ("August", "Aug"), ("September", "Sep", "Sept"),
    ("October", "Oct"), ("November", "Nov"), ("December", "Dec"),
), start=1):
    for name in names:
        MONTHS[name] = index
DATE = re.compile(
    rf"\b(?P<month>{'|'.join(MONTHS)})\.?\s+(?P<first>\d{{1,2}})"
    rf"(?:\s*[-–]\s*(?P<last>\d{{1,2}}))?,?\s+(?P<year>20\d{{2}})\b"
)
ISO_DATE = re.compile(r"\b(?P<year>20\d{2})-(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12]\d|3[01])\b")


class Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.rows: list[list[str]] = []
        self.time_values: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)
            if self._cell is not None:
                self._cell.append(value)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        if tag == "time":
            value = dict(attrs).get("datetime")
            if value:
                self.parts.append(value)
                self.time_values.append(value)
                if self._cell is not None:
                    self._cell.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(" ".join(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell = None


def fetch(url: str) -> bytes:
    request = Request(url, headers={
        "User-Agent": "BarkWatch-calendar-relay/1.0",
        "Accept": "text/html,application/xhtml+xml",
    })
    with urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"official source returned HTTP {response.status}")
        body = response.read(2_000_001)
    if not body or len(body) > 2_000_000:
        raise RuntimeError("official source body size is invalid")
    return body


def _event(event_type: str, local: datetime, source_ref: str) -> dict[str, object]:
    return {
        "event_type": event_type,
        "event_time": int(local.astimezone(timezone.utc).timestamp() * 1000),
        "source_ref": source_ref,
    }


def _dated_event(label: str, *, event_type: str) -> dict[str, object] | None:
    match = DATE.search(label)
    if match is None:
        match = ISO_DATE.search(label)
    if match is None:
        return None
    values = match.groupdict()
    month = MONTHS[values["month"]] if not values["month"].isdigit() else int(values["month"])
    day = int(values.get("last") or values.get("first") or values["day"])
    hour = 14 if event_type == "FOMC" else 8
    minute = 0 if event_type == "FOMC" else 30
    try:
        local = datetime(int(values["year"]), month, day, hour, minute, tzinfo=ZoneInfo("America/New_York"))
    except ValueError:
        return None
    return _event(event_type, local, match.group(0))


def _bls_occurrences(parser: Text, *, event_type: str) -> list[dict[str, object]]:
    rows: dict[int, dict[str, object]] = {}
    for cells in parser.rows:
        if event_type == "CPI":
            if not any("08:30" in cell for cell in cells):
                continue
        elif not any(cell.strip().casefold() == "employment situation" for cell in cells):
            continue
        event = next((_dated_event(cell, event_type=event_type) for cell in cells if _dated_event(cell, event_type=event_type)), None)
        if event is not None:
            rows[int(event["event_time"])] = event
    if not rows:
        for value in parser.time_values:
            event = _dated_event(value, event_type=event_type)
            if event is not None:
                rows[int(event["event_time"])] = event
    return [rows[key] for key in sorted(rows)]


def _fomc_occurrences(parser: Text) -> list[dict[str, object]]:
    eastern = ZoneInfo("America/New_York")
    rows: dict[int, dict[str, object]] = {}
    heading = re.compile(r"^(20\d{2}) FOMC Meetings$")
    meeting_days = re.compile(r"^(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\*?$")
    active_year: int | None = None
    for index, part in enumerate(parser.parts):
        year_match = heading.match(part)
        if year_match:
            active_year = int(year_match.group(1))
            continue
        if active_year is None or part not in MONTHS or len(part) <= 3:
            continue
        for candidate in parser.parts[index + 1:index + 4]:
            day_match = meeting_days.match(candidate)
            if not day_match:
                continue
            day = int(day_match.group(2) or day_match.group(1))
            try:
                local = datetime(active_year, MONTHS[part], day, 14, 0, tzinfo=eastern)
            except ValueError:
                break
            event = _event("FOMC", local, f"{part} {candidate}, {active_year}")
            rows[int(event["event_time"])] = event
            break
    if not rows:
        text = " ".join(parser.parts)
        for match in DATE.finditer(text):
            event = _dated_event(match.group(0), event_type="FOMC")
            if event is not None:
                rows[int(event["event_time"])] = event
    return [rows[key] for key in sorted(rows)]


def occurrences(body: bytes, *, event_type: str) -> list[dict[str, object]]:
    parser = Text()
    parser.feed(body.decode("utf-8"))
    if event_type == "FOMC":
        return _fomc_occurrences(parser)
    if event_type in {"CPI", "NFP"}:
        return _bls_occurrences(parser, event_type=event_type)
    raise ValueError("unsupported event type")


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    generated_ms = int(generated.timestamp() * 1000)
    cutoff = generated_ms - 24 * 60 * 60_000
    documents = []
    events = []
    for event_type in ("CPI", "FOMC", "NFP"):
        url = SOURCES[event_type]
        body = fetch(url)
        documents.append({
            "event_type": event_type,
            "source_url": url,
            "document_sha256": hashlib.sha256(body).hexdigest(),
            "retrieved_at": generated_ms,
        })
        events.extend(row for row in occurrences(body, event_type=event_type) if row["event_time"] >= cutoff)
    future = {row["event_type"] for row in events if row["event_time"] >= generated_ms}
    if future != set(SOURCES):
        raise RuntimeError("official pages do not provide future FOMC/CPI/NFP coverage")
    payload = {
        "schema_version": "BARKWATCH_OFFICIAL_CALENDAR_RELAY_V1",
        "generated_at": generated_ms,
        "valid_until": int((generated + timedelta(hours=36)).timestamp() * 1000),
        "official_documents": documents,
        "events": sorted(events, key=lambda row: (row["event_time"], row["event_type"], row["source_ref"])),
    }
    Path("official-calendar-relay.json").write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
