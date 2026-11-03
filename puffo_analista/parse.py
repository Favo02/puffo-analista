"""Parse puffo-coach monthly context exports (.md with XML-like sections).

Every problem found while parsing (unknown tags/attributes, malformed values,
duplicated data across files) is collected as a `Issue` instead of raising, so
the dashboard can show it to the user.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

# --------------------------------------------------------------------------- #
# Schema: what we know how to read. Anything else triggers a warning.
# --------------------------------------------------------------------------- #

S = dict  # readability alias


def _node(attrs=(), children=None, text=False):
    return S(attrs=set(attrs), children=children or {}, text=text)


HR_STATS = ("avg_hr", "min_hr", "max_hr")
HRV_STATS = ("avg_hrv", "min_hrv", "max_hrv")

SCHEMA: dict[str, dict] = {
    "meals": _node(
        children={
            "day": _node(
                ["date"],
                {"meal": _node(["time", "type"], text=True)},
            )
        }
    ),
    "vitals": _node(
        children={
            "day": _node(
                ["date", "active_minutes", "pai_total", "resting_hr", "steps",
                 "training_load", "vo2max"],
                {
                    "hr_buckets": _node(
                        children={
                            "bucket": _node(["start", "end", *HR_STATS, *HRV_STATS])
                        }
                    )
                },
            )
        }
    ),
    "sleep": _node(
        children={
            "night": _node(
                ["date", "score", "total_min", "deep", "light", "rem", "awake",
                 "wakes", "start", "end", "resp_rate", "sleep_hrv", "sleep_rhr",
                 "spo2_night"]
            )
        }
    ),
    "activities": _node(
        children={
            "activity": _node(
                ["date", "type", "name", "duration", "distance_km", "elevation_m",
                 *HR_STATS, "avg_cadence", "relative_effort"],
                {
                    "splits": _node(
                        children={"km": _node(["n", "time", "avg_hr", "elev_diff"])}
                    ),
                    "hr_chunks": _node(
                        children={"chunk": _node(["n", "start", "end", *HR_STATS])}
                    ),
                },
            )
        }
    ),
}

KNOWN_ACTIVITY_TYPES = {"Run", "Ride", "Hike", "Workout"}

HEADER_RE = re.compile(
    r"^#\s*Personal Context:\s*(\d{4}-\d{2}-\d{2})\s*(?:→|->)\s*(\d{4}-\d{2}-\d{2})\s*$"
)
BARE_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")


# --------------------------------------------------------------------------- #
# Result containers
# --------------------------------------------------------------------------- #


@dataclass
class Issue:
    level: str  # "warning" | "error"
    file: str
    message: str


@dataclass
class FileData:
    path: Path
    range_start: date | None = None
    range_end: date | None = None
    vitals: list[dict] = field(default_factory=list)
    hr_buckets: list[dict] = field(default_factory=list)
    sleep: list[dict] = field(default_factory=list)
    activities: list[dict] = field(default_factory=list)
    splits: list[dict] = field(default_factory=list)
    hr_chunks: list[dict] = field(default_factory=list)
    meals: list[dict] = field(default_factory=list)  # kept raw, not analysed yet


# --------------------------------------------------------------------------- #
# Value conversion helpers
# --------------------------------------------------------------------------- #


class _Conv:
    """Converts attribute strings, recording an issue on failure."""

    def __init__(self, issues: list[Issue], file: str):
        self.issues = issues
        self.file = file

    def _fail(self, where: str, key: str, raw: str, kind: str):
        self.issues.append(
            Issue("warning", self.file, f"{where}: cannot read {key}={raw!r} as {kind}; ignored")
        )
        return None

    def num(self, el: ET.Element, key: str, where: str, kind=float):
        raw = el.get(key)
        if raw is None or raw == "":
            return None
        try:
            return kind(raw)
        except ValueError:
            return self._fail(where, key, raw, kind.__name__)

    def date(self, el: ET.Element, key: str, where: str):
        raw = el.get(key)
        if raw is None:
            return self._fail(where, key, "<missing>", "date")
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return self._fail(where, key, raw, "date")

    def clock(self, el: ET.Element, key: str, where: str):
        """HH:MM -> minutes since midnight."""
        raw = el.get(key)
        if raw is None:
            return None
        m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw)
        if not m or int(m[1]) > 24 or int(m[2]) > 59:
            return self._fail(where, key, raw, "HH:MM time")
        return int(m[1]) * 60 + int(m[2])

    def duration(self, el: ET.Element, key: str, where: str):
        """M:SS / MM:SS / HH:MM:SS -> seconds."""
        raw = el.get(key)
        if raw is None:
            return None
        secs = parse_duration(raw)
        return secs if secs is not None else self._fail(where, key, raw, "duration")


def parse_duration(raw: str) -> int | None:
    parts = raw.strip().split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    if any(len(p) != 2 for p in parts[1:]):  # minutes/seconds are always two digits
        return None
    nums = [int(p) for p in parts]
    if any(n >= 60 for n in nums[1:]):
        return None
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return nums[0] * 3600 + nums[1] * 60 + nums[2]


# --------------------------------------------------------------------------- #
# Schema validation
# --------------------------------------------------------------------------- #


def _validate(el: ET.Element, spec: dict, path: str, issues: list[Issue], file: str):
    unknown_attrs = set(el.attrib) - spec["attrs"]
    if unknown_attrs:
        issues.append(
            Issue("warning", file, f"<{path}>: unknown attribute(s) {sorted(unknown_attrs)} ignored")
        )
    if not spec["text"] and el.text and el.text.strip():
        issues.append(Issue("warning", file, f"<{path}>: unexpected text {el.text.strip()[:60]!r} ignored"))
    for child in el:
        if child.tail and child.tail.strip():
            issues.append(
                Issue("warning", file, f"<{path}>: unexpected text {child.tail.strip()[:60]!r} ignored")
            )
        child_spec = spec["children"].get(child.tag)
        if child_spec is None:
            issues.append(Issue("warning", file, f"<{path}>: unknown element <{child.tag}> ignored"))
            continue
        _validate(child, child_spec, f"{path}/{child.tag}", issues, file)


# --------------------------------------------------------------------------- #
# Section extractors
# --------------------------------------------------------------------------- #


def _extract_vitals(root: ET.Element, fd: FileData, c: _Conv):
    for day in root.findall("day"):
        d = c.date(day, "date", "vitals/day")
        if d is None:
            continue
        w = f"vitals {d}"
        fd.vitals.append(
            dict(
                date=d,
                steps=c.num(day, "steps", w, int),
                resting_hr=c.num(day, "resting_hr", w),
                active_minutes=c.num(day, "active_minutes", w),
                training_load=c.num(day, "training_load", w),
                pai_total=c.num(day, "pai_total", w),
                vo2max=c.num(day, "vo2max", w),
            )
        )
        for b in day.findall("hr_buckets/bucket"):
            start = c.clock(b, "start", w)
            fd.hr_buckets.append(
                dict(
                    date=d,
                    start_min=start,
                    end_min=c.clock(b, "end", w),
                    label=f"{b.get('start')}–{b.get('end')}",
                    **{k: c.num(b, k, w) for k in (*HR_STATS, *HRV_STATS)},
                )
            )


def _extract_sleep(root: ET.Element, fd: FileData, c: _Conv):
    for n in root.findall("night"):
        d = c.date(n, "date", "sleep/night")
        if d is None:
            continue
        w = f"sleep {d}"
        row = dict(date=d, start_min=c.clock(n, "start", w), end_min=c.clock(n, "end", w))
        for k in ("score", "total_min", "deep", "light", "rem", "awake", "wakes",
                  "resp_rate", "sleep_hrv", "sleep_rhr", "spo2_night"):
            row[k] = c.num(n, k, w)
        fd.sleep.append(row)


def _extract_activities(root: ET.Element, fd: FileData, c: _Conv):
    for i, a in enumerate(root.findall("activity")):
        d = c.date(a, "date", "activities/activity")
        if d is None:
            continue
        name = a.get("name") or "(unnamed)"
        w = f"activity {d} {name!r}"
        atype = a.get("type") or "Other"
        if atype not in KNOWN_ACTIVITY_TYPES:
            c.issues.append(
                Issue("warning", c.file,
                      f"{w}: unknown activity type {atype!r}, shown with generic min/km units")
            )
        aid = f"{fd.path.name}#{i}"
        fd.activities.append(
            dict(
                id=aid,
                date=d,
                type=atype,
                name=name,
                duration_s=c.duration(a, "duration", w),
                distance_km=c.num(a, "distance_km", w),
                elevation_m=c.num(a, "elevation_m", w),
                avg_cadence=c.num(a, "avg_cadence", w),
                relative_effort=c.num(a, "relative_effort", w),
                **{k: c.num(a, k, w) for k in HR_STATS},
            )
        )
        for km in a.findall("splits/km"):
            fd.splits.append(
                dict(
                    activity_id=aid,
                    n=c.num(km, "n", w, int),
                    time_s=c.duration(km, "time", w),
                    avg_hr=c.num(km, "avg_hr", w),
                    elev_diff=c.num(km, "elev_diff", w),
                )
            )
        for ch in a.findall("hr_chunks/chunk"):
            fd.hr_chunks.append(
                dict(
                    activity_id=aid,
                    n=c.num(ch, "n", w, int),
                    start_s=c.duration(ch, "start", w),
                    end_s=c.duration(ch, "end", w),
                    **{k: c.num(ch, k, w) for k in HR_STATS},
                )
            )


def _extract_meals(root: ET.Element, fd: FileData, c: _Conv):
    for idx, day in enumerate(root.findall("day")):
        d = c.date(day, "date", "meals/day")
        if d is None:
            continue
        for m in day.findall("meal"):
            fd.meals.append(
                dict(date=d, day_idx=idx, time=m.get("time"), type=m.get("type"),
                     text=(m.text or "").strip())
            )


EXTRACTORS = {
    "meals": _extract_meals,
    "vitals": _extract_vitals,
    "sleep": _extract_sleep,
    "activities": _extract_activities,
}


# --------------------------------------------------------------------------- #
# File parsing
# --------------------------------------------------------------------------- #


def parse_file(path: Path, issues: list[Issue]) -> FileData | None:
    file = path.name
    fd = FileData(path=path)
    text = path.read_text(encoding="utf-8")

    body_lines = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            m = HEADER_RE.match(line.strip())
            if m:
                fd.range_start, fd.range_end = (date.fromisoformat(x) for x in m.groups())
            else:
                issues.append(Issue("warning", file, f"unexpected heading {line.strip()!r} ignored"))
            continue
        body_lines.append(line)
    if fd.range_start is None:
        issues.append(Issue("warning", file, "missing '# Personal Context: A → B' header"))

    body = BARE_AMP_RE.sub("&amp;", "\n".join(body_lines))
    try:
        root = ET.fromstring(f"<root>{body}</root>")
    except ET.ParseError as e:
        issues.append(Issue("error", file, f"cannot parse file ({e}); file skipped"))
        return None

    if root.text and root.text.strip():
        issues.append(Issue("warning", file, f"unexpected text {root.text.strip()[:60]!r} ignored"))

    seen = set()
    for section in root:
        if section.tail and section.tail.strip():
            issues.append(Issue("warning", file, f"unexpected text {section.tail.strip()[:60]!r} ignored"))
        spec = SCHEMA.get(section.tag)
        if spec is None:
            issues.append(Issue("warning", file, f"unknown section <{section.tag}> ignored"))
            continue
        if section.tag in seen:
            issues.append(Issue("warning", file, f"section <{section.tag}> appears more than once"))
        seen.add(section.tag)
        _validate(section, spec, section.tag, issues, file)
        EXTRACTORS[section.tag](section, fd, _Conv(issues, file))

    if fd.range_start and fd.range_end:
        _check_in_range(fd, issues)
    return fd


def _check_in_range(fd: FileData, issues: list[Issue]):
    for section in ("vitals", "sleep", "activities", "meals"):
        outside = sorted(
            {str(r["date"]) for r in getattr(fd, section)
             if not fd.range_start <= r["date"] <= fd.range_end}
        )
        if outside:
            issues.append(
                Issue("warning", fd.path.name,
                      f"{section}: dates outside the header range "
                      f"{fd.range_start} → {fd.range_end}: {', '.join(outside)}")
            )


# --------------------------------------------------------------------------- #
# Loading a whole folder, with duplicate detection
# --------------------------------------------------------------------------- #

# section -> child tables keyed by parent id
CHILD_TABLES = {"vitals": ["hr_buckets"], "activities": ["splits", "hr_chunks"]}


@dataclass
class Dataset:
    files: list[FileData]
    issues: list[Issue]
    tables: dict[str, list[dict]]


def load_folder(folder: Path) -> Dataset:
    issues: list[Issue] = []
    files: list[FileData] = []
    for path in sorted(folder.glob("*.md")):
        fd = parse_file(path, issues)
        if fd is not None:
            files.append(fd)

    _check_range_overlap(files, issues)

    tables: dict[str, list[dict]] = {
        k: [] for k in ("vitals", "hr_buckets", "sleep", "activities", "splits", "hr_chunks", "meals")
    }

    # Per-section duplicate detection on dates. Activities can legitimately
    # be several per day, but only within a single file.
    for section in ("vitals", "sleep", "activities", "meals"):
        owners: dict[date, set[str]] = {}
        day_elems: dict[tuple[str, date], set] = {}
        for fd in files:
            for i, r in enumerate(getattr(fd, section)):
                owners.setdefault(r["date"], set()).add(fd.path.name)
                # one <day>/<night> element per row, except meals (many rows per <day>)
                day_elems.setdefault((fd.path.name, r["date"]), set()).add(r.get("day_idx", i))
        counts = {k: len(v) for k, v in day_elems.items()}

        bad: set[date] = set()
        for d, fs in owners.items():
            if len(fs) > 1:
                bad.add(d)
                issues.append(
                    Issue("error", ", ".join(sorted(fs)),
                          f"{section} for {d} present in multiple files; data for that day NOT loaded")
                )
        if section in ("vitals", "sleep", "meals"):
            for (f, d), n in counts.items():
                if n > 1 and d not in bad:
                    bad.add(d)
                    issues.append(
                        Issue("error", f, f"{section} for {d} appears {n} times; data for that day NOT loaded")
                    )

        for fd in files:
            kept = [r for r in getattr(fd, section) if r["date"] not in bad]
            tables[section].extend(kept)
            for child in CHILD_TABLES.get(section, []):
                if section == "vitals":
                    tables[child].extend(r for r in getattr(fd, child) if r["date"] not in bad)
                else:
                    ids = {r["id"] for r in kept}
                    tables[child].extend(r for r in getattr(fd, child) if r["activity_id"] in ids)

    return Dataset(files=files, issues=issues, tables=tables)


def _check_range_overlap(files: list[FileData], issues: list[Issue]):
    ranged = sorted((f for f in files if f.range_start), key=lambda f: f.range_start)
    for a, b in zip(ranged, ranged[1:]):
        if b.range_start <= a.range_end:
            issues.append(
                Issue("error", f"{a.path.name}, {b.path.name}",
                      f"file date ranges overlap ({a.range_start}→{a.range_end} and "
                      f"{b.range_start}→{b.range_end}); days present in both are NOT loaded")
            )


# --------------------------------------------------------------------------- #
# Small helpers shared with the dashboard
# --------------------------------------------------------------------------- #


def night_after_day(sleep_date: date, start_min: int | None) -> date:
    """The day whose evening precedes this sleep.

    The sleep date is the day you fell asleep: 23:30 on D belongs to D (the
    evening after day D), 00:45 on D belongs to the evening of D-1.
    """
    if start_min is not None and start_min >= 12 * 60:
        return sleep_date
    return sleep_date - timedelta(days=1)


def sleep_datetimes(sleep_date: date, start_min: int | None, end_min: int | None):
    if start_min is None:
        return None, None
    start = datetime.combine(sleep_date, datetime.min.time()) + timedelta(minutes=start_min)
    if end_min is None:
        return start, None
    end = datetime.combine(sleep_date, datetime.min.time()) + timedelta(minutes=end_min)
    if end <= start:
        end += timedelta(days=1)
    return start, end
