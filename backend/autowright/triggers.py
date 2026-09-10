"""Trigger math and display strings (§4.1, §4.3): the cron dialect, the
interval dialect, one-shot times, next occurrences, humanized labels, and
trigger validation."""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from . import timefmt

# §4.3 enable stamp: the moment the trigger last became live, a §5 stored
# timestamp. Backend-owned — stamp_enabled is the only writer.
ENABLED_AT = "enabledAt"

# §4.3 discord `secret` = a §4.8 secret id (uuid, lowercase hyphenated — the §4 id form).
SECRET_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

DOW_LONG = ["Sundays", "Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays"]
DOW_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

RESERVED_KINDS = ("pubsub",)  # §4.3 message triggers — coming soon

# §4.3 one-shot past check - normalize_triggers matches on this exact value to
# apply the spent-drop rule, so the two must share one constant.
PAST_ERROR = "the time must be in the future"

# Unsatisfiable expressions (e.g. "0 0 30 2 *") stop searching after this many days.
_SEARCH_DAYS = 366 * 5


class CronError(ValueError):
    pass


# ---------- interval dialect (§4.3): P[nD][T[nH][nM][nS]], 1 min .. 365 days ----------

SCHEDULE_KINDS = ("cron", "interval")  # §4.3: the kinds the §8 sync derives; carry `source`
INTERVAL_MIN_S = 15  # §4.3: one scheduler tick at the default cadence (§15)
INTERVAL_MAX_S = 365 * 86400
INTERVAL_FORMAT_ERROR = ("an interval needs an ISO-8601 duration like PT6H "
                         "(days, hours, minutes, seconds)")
INTERVAL_MIN_ERROR = "an interval must be at least 15 seconds"
INTERVAL_MAX_ERROR = "an interval can be at most 365 days"
_DURATION_RE = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")
# Canonical units, largest first: the stored form is one component in the
# largest unit that divides the total exactly (§4.3 interval dialect).
_UNITS = (("D", 86400, "day", "d"), ("H", 3600, "hour", "h"),
          ("M", 60, "minute", "m"), ("S", 1, "second", "s"))


class IntervalError(ValueError):
    pass


def parse_duration(every: object) -> int:
    """§4.3 interval dialect → total seconds. Raises IntervalError with the
    plain-word reason (format, under 15 seconds, over 365 days)."""
    if not isinstance(every, str):
        raise IntervalError(INTERVAL_FORMAT_ERROR)
    m = _DURATION_RE.match(every.strip().upper())
    if not m or not any(m.groups()) or every.strip().upper().endswith("T"):
        raise IntervalError(INTERVAL_FORMAT_ERROR)
    d, h, mi, sec = (int(g) if g else 0 for g in m.groups())
    total = d * 86400 + h * 3600 + mi * 60 + sec
    if total == 0:
        raise IntervalError(INTERVAL_FORMAT_ERROR)
    if total < INTERVAL_MIN_S:
        raise IntervalError(INTERVAL_MIN_ERROR)
    if total > INTERVAL_MAX_S:
        raise IntervalError(INTERVAL_MAX_ERROR)
    return total


def _canonical_parts(seconds: int) -> tuple[int, tuple[str, int, str, str]]:
    for unit in _UNITS:
        if seconds % unit[1] == 0:
            return seconds // unit[1], unit
    raise AssertionError("seconds always divide by 1")


def canonical_duration(seconds: int) -> str:
    """§4.3 canonical form: exactly one component, the largest unit that
    divides the total — PT360M → PT6H, PT24H → P1D, PT1H30M → PT90M."""
    n, (letter, _, _, _) = _canonical_parts(seconds)
    return f"P{n}D" if letter == "D" else f"PT{n}{letter}"


def interval_display(every: str) -> tuple[str, str]:
    """§4.3 interval labels: "Every 6 hours" / "Every 6h"; a count of 1 reads
    as the bare unit ("Every hour" / "Every 1h"). No timezone suffix."""
    n, (_, _, word, short) = _canonical_parts(parse_duration(every))
    long = f"Every {word}" if n == 1 else f"Every {n} {word}s"
    return long, f"Every {n}{short}"


def interval_anchor(t: dict, run_baseline: datetime | None, fallback: datetime) -> datetime:
    """§4.3 interval semantics: the later of the trigger's enable stamp and
    the automation's run baseline; `fallback` (the request moment) when the
    caller has neither — the §19 preview, which anchors at now."""
    cands = [x for x in (enabled_since(t), run_baseline) if x is not None]
    return max(cands) if cands else fallback


def interval_next(t: dict, after: datetime, run_baseline: datetime | None) -> datetime:
    """First `anchor + n × every` (n ≥ 1) strictly after `after`."""
    every = timedelta(seconds=parse_duration(t["every"]))
    anchor = interval_anchor(t, run_baseline, after)
    if after < anchor:
        return anchor + every
    n = int((after - anchor) / every) + 1
    return anchor + n * every


# ---------- cron dialect (§4.3): 5 fields, numbers only, * , - / ----------

_FIELD_NAMES = ["minute", "hour", "day-of-month", "month", "day-of-week"]
_FIELD_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]


def _ascii_digits(s: str) -> bool:
    """ASCII digits only — `str.isdigit()` accepts characters `int()` rejects
    (e.g. '²' raises ValueError), and the renderer's parser is ASCII-only, so
    the two dialects must agree."""
    return bool(s) and s.isascii() and s.isdigit()


def _parse_field(text: str, name: str, lo: int, hi: int) -> tuple[set[int], bool]:
    """One cron field → (matching values, is unrestricted `*`)."""
    out: set[int] = set()
    if not text:
        raise CronError(f"{name} field is empty")
    for item in text.split(","):
        body, sep, step_s = item.partition("/")
        step = 1
        if sep:
            # `sep` not `step_s`: a trailing slash ("5/") must be rejected,
            # matching the renderer's parser — not silently read as step 1.
            if not _ascii_digits(step_s) or int(step_s) < 1:
                raise CronError(f"{name}: bad step {item!r}")
            step = int(step_s)
        if body == "*":
            a, b = lo, hi
        elif "-" in body:
            a_s, _, b_s = body.partition("-")
            if not (_ascii_digits(a_s) and _ascii_digits(b_s)):
                raise CronError(f"{name}: bad range {item!r}")
            a, b = int(a_s), int(b_s)
        elif _ascii_digits(body):
            a = b = int(body)
        else:
            raise CronError(f"{name}: bad value {item!r} (numbers only)")
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise CronError(f"{name}: {item!r} out of range {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return out, text == "*"


def parse_cron(expression: str) -> list[tuple[set[int], bool]]:
    """Validate + expand a §4.3 cron expression; raises CronError."""
    if expression is not None and not isinstance(expression, str):
        # A non-string expression (YAML int in an import archive, a raw API
        # payload) must answer the ordinary 422, not crash with AttributeError.
        raise CronError("the cron expression must be a string")
    fields = (expression or "").split()
    if len(fields) != 5:
        raise CronError("a cron expression needs 5 fields (minute hour day month weekday)")
    return [_parse_field(f, n, lo, hi)
            for f, n, (lo, hi) in zip(fields, _FIELD_NAMES, _FIELD_RANGES)]


def cron_next(expression: str, after: datetime | None = None) -> datetime | None:
    """Next match strictly after `after` (local wall clock), None if unsatisfiable."""
    (mins, _), (hours, _), (doms, dom_star), (months, _), (dows, dow_star) = parse_cron(expression)
    t = (after or datetime.now()).replace(second=0, microsecond=0) + timedelta(minutes=1)
    hhmm = [(hh, mm) for hh in sorted(hours) for mm in sorted(mins)]
    day = t.date()
    for _ in range(_SEARCH_DAYS):
        if day.month in months:
            spec_dow = (day.weekday() + 1) % 7  # weekday(): Mon=0 → spec Sun=0
            # Vixie rule: both dom and dow restricted → a date matching either fires.
            if (day.day in doms if dow_star else
                    spec_dow in dows if dom_star else
                    day.day in doms or spec_dow in dows):
                floor = t if day == t.date() else datetime.combine(day, time.min)
                for hh, mm in hhmm:
                    cand = datetime(day.year, day.month, day.day, hh, mm)
                    if cand >= floor:
                        return cand
        day += timedelta(days=1)
    return None


# ---------- timezone (§4.3 `timezone`): wall clock in the trigger's zone ----------

def zone_of(t: dict) -> ZoneInfo | None:
    """The trigger's zone, None when local. Assumes a validated `timezone`."""
    return ZoneInfo(t["timezone"]) if t.get("timezone") else None


def _to_wall(local: datetime, zone: ZoneInfo) -> datetime:
    """Local naive → the zone's naive wall clock."""
    return local.astimezone(zone).replace(tzinfo=None)


def _round_trips(wall: datetime, zone: ZoneInfo) -> bool:
    """True when `wall` is a real wall time in `zone` (not erased by a
    spring-forward gap)."""
    return wall.replace(tzinfo=zone, fold=0).astimezone(UTC) \
               .astimezone(zone).replace(tzinfo=None) == wall


def _wall_to_local(wall: datetime, zone: ZoneInfo, after: datetime | None) -> datetime | None:
    """The zone's naive wall clock → local naive, DST-transition-aware.

    The naive `wall.replace(tzinfo=zone)` conversion is non-monotonic around the
    zone's transitions (an ambiguous fall-back time reads as the *earlier*
    instant, which can land before `after` and make the scheduler re-fire one
    occurrence every tick). Rules here: an ambiguous wall time picks the
    earliest reading strictly after `after` (§4.3: one repeated by fall-back
    fires once); a nonexistent wall time fires at the next valid minute
    (§4.3); returns None when every reading is ≤ `after`."""
    d0, d1 = wall.replace(tzinfo=zone, fold=0), wall.replace(tzinfo=zone, fold=1)
    if d0.utcoffset() != d1.utcoffset() and not _round_trips(wall, zone):
        # Erased by the spring-forward gap: fold=0 reads it with the
        # pre-transition offset, i.e. the occurrence fires shifted forward by
        # the gap width ("2:30" fires at 3:30) — §4.3, and the renderer's
        # cron.ts + the shared parity fixture implement the same rule.
        loc = d0.astimezone().replace(tzinfo=None)
        return loc if after is None or loc > after else None
    readings = sorted({d.timestamp(): d for d in (d0, d1)}.values(),
                      key=lambda d: d.timestamp())
    for d in readings:
        loc = d.astimezone().replace(tzinfo=None)
        if after is None or loc > after:
            return loc
    return None


def _local_gap_fix(d: datetime, after: datetime | None) -> datetime | None:
    """§4.3 gap rule for the system zone (no `timezone` on the trigger): a
    wall time erased by spring-forward fires shifted forward by the gap width,
    same as `_wall_to_local` does for a zoned trigger. An ordinary or
    ambiguous reading returns unchanged - the scheduler's naive baseline math
    already fires a fall-back hour once. Returns None only when the shifted
    reading lands at or before `after`."""
    d0, d1 = d.replace(fold=0), d.replace(fold=1)
    if d0.astimezone().utcoffset() == d1.astimezone().utcoffset():
        return d
    # Transition zone: a round trip through UTC tells erased from ambiguous -
    # an ambiguous wall time survives it, an erased one comes back shifted.
    rt = d0.astimezone(UTC).astimezone().replace(tzinfo=None)
    if rt == d:
        return d
    return rt if after is None or rt > after else None


def _to_local(wall: datetime, zone: ZoneInfo) -> datetime:
    """The zone's naive wall clock → local naive (first/earliest reading —
    use `_wall_to_local` when monotonicity against a baseline matters)."""
    return _wall_to_local(wall, zone, None) or wall.replace(tzinfo=zone).astimezone().replace(tzinfo=None)


def timezone_error(timezone) -> str | None:
    """Error message for an unusable timezone value, None when valid (or absent)."""
    if timezone is None:
        return None
    try:
        if not isinstance(timezone, str):
            raise ValueError
        ZoneInfo(timezone)
    except Exception:  # noqa: BLE001 — ZoneInfoNotFoundError, ValueError, ...
        return f"unknown timezone {timezone!r} — use an IANA name like Asia/Tokyo"
    return None


def _timezone_suffix(timezone: str | None) -> str:
    """§4.3: labels append the zone's city — last IANA segment, _ → space."""
    return f" ({timezone.rsplit('/', 1)[-1].replace('_', ' ')})" if timezone else ""


# ---------- triggers (§4.3) ----------

def normalize_handle(frm: str) -> str:
    """§4.3 imessage `from` normalization: emails pass through; phones drop
    the obvious formatting (spaces, dashes, dots, parentheses) so
    "+1 (555) 123-4567" stores as the E.164 form Messages matches on."""
    frm = frm.strip()
    return frm if "@" in frm else re.sub(r"[\s().\-]", "", frm)


def normalize_authors(raw: list) -> list[str]:
    """§4.3 discord `author` normalization: trimmed, deduped, sorted — element
    order must never distinguish two triggers (the merge identity compares
    the normalized list)."""
    return sorted({str(a).strip() for a in raw})


RUN_IF_MISSED = "runIfMissed"  # §4.3: cron/interval/time only, stored only when false
RUN_IF_MISSED_ERROR = "run if missed must be true or false"


def run_if_missed(t: dict) -> bool:
    """§4.3 `runIfMissed` as the scheduler reads it: absent = true (every
    trigger stored before the field existed keeps the §6 wake catch-up)."""
    return t.get(RUN_IF_MISSED, True) is not False


def validate_trigger(t: dict, allow_past: bool = False) -> str | None:
    """§19 PATCH rule: error message, or None when the trigger is storable.
    `allow_past` skips the future check for one-shots — an EXISTING stored
    trigger whose moment elapsed must not 422 every unrelated edit of the
    list it rides in (the scheduler consumes it on its own, §4.3)."""
    kind = t.get("kind")
    if kind in RESERVED_KINDS:
        return f"{kind} triggers are coming soon"
    if kind == "app_start":
        return None
    if kind == "discord":
        # §4.3: channel = ASCII-digit snowflake; secret = the §4.8 id of the
        # secret holding the bot token (existence is a `connection` concern,
        # not a 422 — a mid-edit secret deletion must never block a save).
        ch = t.get("channel")
        if not (isinstance(ch, str) and _ascii_digits(ch.strip())):
            return "the Discord channel must be its numeric channel id"
        sec = t.get("secret")
        if not (isinstance(sec, str) and SECRET_ID_RE.match(sec.strip())):
            return "a Discord trigger needs the id of the secret holding the bot token"
        pat = t.get("pattern")
        if pat is not None and not (isinstance(pat, str) and pat.strip()):
            return "the Discord message pattern must be a nonempty text"
        if not isinstance(t.get("mention", False), bool):
            return "the Discord mention flag must be true or false"
        au = t.get("author")
        if au is not None and not (isinstance(au, list) and au and all(
                isinstance(a, str) and _ascii_digits(a.strip()) for a in au)):
            return "the Discord sender filter must be a list of numeric user ids"
        return None
    if kind == "imessage":
        # §4.3: from = sender handle — an email, or an E.164 phone matching
        # the form Messages stores. Formatting strips at save; a number
        # without the country code is refused outright, because it could
        # never match a stored handle (a trigger that silently never fires
        # is the worst failure mode).
        frm = t.get("from")
        if not (isinstance(frm, str) and frm.strip()):
            return ("an iMessage trigger needs the sender's handle — a phone "
                    "in +15551234567 form or an email")
        frm = frm.strip()
        if "@" in frm:
            if any(c.isspace() for c in frm):
                return "the sender email can't contain spaces"
        elif not re.fullmatch(r"\+[0-9]{3,15}", normalize_handle(frm)):
            return ("a phone sender needs the international form with the "
                    "country code, like +15551234567 — or use an email")
        pat = t.get("pattern")
        if pat is not None and not (isinstance(pat, str) and pat.strip()):
            return "the iMessage message pattern must be a nonempty text"
        return None
    if kind == "cron":
        # §4.3 provenance: required — every ingest path stamps it.
        if t.get("source") not in ("spec", "user"):
            return 'a cron trigger\'s source must be "spec" or "user"'
        if RUN_IF_MISSED in t and not isinstance(t[RUN_IF_MISSED], bool):
            return RUN_IF_MISSED_ERROR
        if err := timezone_error(t.get("timezone")):
            return err
        try:
            parse_cron(t.get("expression") or "")
        except CronError as e:
            return str(e)
        return None
    if kind == "interval":
        # §4.3 provenance: required, like a cron. No timezone — a duration has
        # no wall clock; a sent one is ignored and never stored.
        if t.get("source") not in ("spec", "user"):
            return 'an interval trigger\'s source must be "spec" or "user"'
        if RUN_IF_MISSED in t and not isinstance(t[RUN_IF_MISSED], bool):
            return RUN_IF_MISSED_ERROR
        try:
            parse_duration(t.get("every"))
        except IntervalError as e:
            return str(e)
        return None
    if kind == "time":
        if RUN_IF_MISSED in t and not isinstance(t[RUN_IF_MISSED], bool):
            return RUN_IF_MISSED_ERROR
        if err := timezone_error(t.get("timezone")):
            return err
        try:
            at = datetime.fromisoformat(t.get("at") or "")
        except (TypeError, ValueError):
            return "invalid timestamp — use local ISO format like 2026-07-20T15:00"
        if at.tzinfo is not None:
            # An offset-aware `at` would make the naive comparison below (and
            # trigger_next) raise TypeError — the zone belongs in `timezone`.
            return "the timestamp must not carry a UTC offset — use timezone for the zone"
        zone = zone_of(t)
        # The zone-less reading goes through the same gap fix trigger_next and
        # time_elapsed apply, so all three agree in the spring-forward gap.
        local = _to_local(at, zone) if zone else (_local_gap_fix(at, None) or at)
        if not allow_past and local <= datetime.now():
            return PAST_ERROR
        return None
    return f"unknown trigger kind {kind!r}"


def normalize_triggers(raw: list,
                       existing_ids: set[str] | None = None) -> tuple[list[dict], str | None]:
    """Validate a whole list; assign ids to new entries. → (stored shape, error).

    `existing_ids` is the set of trigger ids already stored on the automation:
    only those revalidate leniently (allow_past), so an elapsed one-shot can't
    block edits or version saves of the whole list. An id-carrying past `time`
    entry the automation does *not* store is the §4.3 spent case (a staged
    one-shot that elapsed before the save, or one the scheduler consumed
    mid-edit): dropped silently, never stored, never a 422 - so a fabricated
    id still can't smuggle a past time into storage. None (the §19 preview,
    which has no automation context) trusts any id — display-only, nothing is
    stored there."""
    out: list[dict] = []
    for t in raw or []:
        if not isinstance(t, dict):
            return [], "each trigger must be an object"
        known = bool(t.get("id")) and (existing_ids is None or t["id"] in existing_ids)
        err = validate_trigger(t, allow_past=known)
        if err == PAST_ERROR and t.get("id") and existing_ids is not None:
            continue  # §4.3 spent-drop: staged one-shot elapsed before the save
        if err:
            return [], err
        n: dict = {"id": t.get("id") or str(uuid.uuid4()),
                   "kind": t["kind"], "enabled": bool(t.get("enabled", True))}
        if t["kind"] == "cron":
            n["expression"] = t["expression"].strip()
            n["source"] = t["source"]  # §4.3: required, stored as sent
        elif t["kind"] == "interval":
            # §4.3: stored canonical, whatever spelling arrived — one trigger
            # per duration, so the merge and the archive compare one string.
            n["every"] = canonical_duration(parse_duration(t["every"]))
            n["source"] = t["source"]
        elif t["kind"] == "time":
            n["at"] = t["at"]
        elif t["kind"] == "discord":
            n["channel"] = t["channel"].strip()
            n["secret"] = t["secret"].strip()
            if t.get("pattern"):
                n["pattern"] = t["pattern"].strip()
            if t.get("mention"):
                n["mention"] = True
            if t.get("author"):
                n["author"] = normalize_authors(t["author"])
        elif t["kind"] == "imessage":
            n["from"] = normalize_handle(t["from"])
            if t.get("pattern"):
                n["pattern"] = t["pattern"].strip()
        else:  # app_start — no fields, at most one per automation (§4.3)
            if any(x["kind"] == "app_start" for x in out):
                return [], "only one app-start trigger per automation"
        # §4.3: `timezone` belongs to cron/time only — the loader drops it for
        # any other kind, so keeping it here would survive only until restart.
        if t["kind"] in ("cron", "time") and t.get("timezone"):
            n["timezone"] = t["timezone"]
        # §4.3 `runIfMissed`: cron/interval/time only, stored only when false
        # (absent = true, the pre-field shape, §21); ignored on every other kind.
        if t["kind"] in ("cron", "interval", "time") and t.get(RUN_IF_MISSED) is False:
            n[RUN_IF_MISSED] = False
        out.append(n)
    return out, None


def stamp_enabled(new: list[dict], old: list[dict] | None = None,
                  now_iso: str | None = None) -> list[dict]:
    """§4.3 enable stamp: reconcile an incoming trigger list against the stored
    one by id, returning the list to store. An entry that arrives enabled and
    wasn't (a create, an off-to-on edit) gets a fresh `enabledAt`; one that was
    already on carries its stamp forward, so changing a live cron's expression
    is not a re-enable. A disabled entry keeps whatever stamp it had — the next
    on-transition overwrites it. A stored trigger that predates the field is
    never stamped by a write that doesn't turn it on (§4.3: no self-heal), and
    any client-sent value is discarded — the backend owns this field."""
    prev = {t["id"]: t for t in (old or []) if t.get("id")}
    out = []
    for t in new:
        t = {k: v for k, v in t.items() if k != ENABLED_AT}
        was = prev.get(t.get("id"))
        if t.get("enabled") and not (was and was.get("enabled")):
            t[ENABLED_AT] = now_iso or timefmt.now_iso()
        elif was and was.get(ENABLED_AT):
            t[ENABLED_AT] = was[ENABLED_AT]
        out.append(t)
    return out


def enabled_since(t: dict) -> datetime | None:
    """§4.3 `enabledAt` as the local naive datetime trigger math runs in. None
    for a trigger stored before the field existed, or one whose stamp is
    unreadable (§5 lenient — the §4.1 baseline just falls back)."""
    raw = t.get(ENABLED_AT)
    if not isinstance(raw, str):
        return None
    try:
        return timefmt.parse_local(raw).replace(tzinfo=None)
    except ValueError:
        return None


def _hm(hour: int, minute: int) -> str:
    return f"{hour}:{minute:02d}"


def cron_display(expression: str, timezone: str | None = None) -> tuple[str, str]:
    """§4.3 humanized labels — exactly two simple shapes get words."""
    sfx = _timezone_suffix(timezone)
    p = expression.split()
    if len(p) == 5 and p[0].isdigit() and p[1].isdigit() and p[2] == "*" and p[3] == "*":
        t = _hm(int(p[1]), int(p[0]))
        if p[4] == "*":
            return f"Daily at {t}{sfx}", f"Daily {t}{sfx}"
        if len(p[4]) == 1 and p[4] in "0123456":
            d = int(p[4])
            return f"{DOW_LONG[d]} at {t}{sfx}", f"{DOW_SHORT[d]} {t}{sfx}"
    return expression.strip() + sfx, expression.strip() + sfx


def time_display(at: str, timezone: str | None = None) -> tuple[str, str]:
    dt = datetime.fromisoformat(at)
    sfx = _timezone_suffix(timezone)
    # §4.3: seconds show only when non-zero — matching the renderer's timeLabels.
    ss = f":{dt.second:02d}" if dt.second else ""
    ampm = f"{(dt.hour % 12) or 12}:{dt.minute:02d}{ss} {'AM' if dt.hour < 12 else 'PM'}"
    day = f"{dt.strftime('%b')} {dt.day}"
    return f"Once at {day}, {ampm}{sfx}", f"Once {day} {_hm(dt.hour, dt.minute)}{ss}{sfx}"


def trigger_display(t: dict) -> tuple[str, str]:
    if t["kind"] == "cron":
        return cron_display(t["expression"], t.get("timezone"))
    if t["kind"] == "interval":
        return interval_display(t["every"])
    if t["kind"] == "app_start":
        return "On app start", "App start"
    if t["kind"] == "discord":
        # §11: a missing detail field renders "missing" — never a dangling
        # "Discord · " label on a broken trigger.
        label = f"Discord · {t.get('channel') or 'missing'}"
        if t.get("pattern"):
            label += f" · “{t['pattern']}”"
        return label, "Discord"
    if t["kind"] == "imessage":
        label = f"iMessage · {t.get('from') or 'missing'}"
        if t.get("pattern"):
            label += f" · “{t['pattern']}”"
        return label, "iMessage"
    return time_display(t["at"], t.get("timezone"))


def trigger_next(t: dict, after: datetime | None = None,
                 run_baseline: datetime | None = None) -> datetime | None:
    """Next occurrence of one trigger strictly after `after`, both local naive.
    A `timezone` trigger is evaluated on its zone's wall clock (the enabled flag
    is the caller's concern). `run_baseline` — the automation's latest real
    execution start, else its created_at, local naive — anchors an `interval`
    (§4.3 interval semantics); callers without automation context (the §19
    preview) pass none, and the interval anchors at `after`."""
    if t["kind"] in ("app_start", "discord", "imessage"):
        return None  # §4.3: no computable next occurrence
    base = after or datetime.now()
    if t["kind"] == "interval":
        return interval_next(t, base, run_baseline)
    zone = zone_of(t)
    if t["kind"] == "cron":
        if not zone:
            # Same non-monotonicity as the zoned path, on the system zone: a
            # candidate erased by spring-forward shifts forward by the gap
            # width and must still land strictly after `base`.
            nxt = cron_next(t["expression"], base)
            for _ in range(1000):
                if nxt is None:
                    return None
                loc = _local_gap_fix(nxt, base)
                if loc is not None:
                    return loc
                nxt = cron_next(t["expression"], nxt)
            return None
        # The wall→local map is non-monotonic around DST transitions; keep
        # advancing until a reading lands strictly after `base` — the
        # scheduler's contract (occurrences at or before the baseline never
        # fire) depends on it.
        nxt = cron_next(t["expression"], _to_wall(base, zone))
        for _ in range(1000):
            if nxt is None:
                return None
            loc = _wall_to_local(nxt, zone, base)
            if loc is not None:
                return loc
            nxt = cron_next(t["expression"], nxt)
        return None
    at = datetime.fromisoformat(t["at"])
    if zone:
        at = _to_local(at, zone)
    else:
        at = _local_gap_fix(at, None) or at
    return at if at > base else None


def time_elapsed(t: dict, now: datetime | None = None) -> bool:
    """§4.3: has a one-shot's moment passed? A spent trigger is consumed
    whether it fired or was missed — it never lingers."""
    if t.get("kind") != "time":
        return False
    try:
        at = datetime.fromisoformat(t["at"])
    except (KeyError, TypeError, ValueError):
        # §4.3: only a *parsable* past `at` counts as spent. An unreadable one
        # — an unquoted timestamp YAML loaded as a datetime, say — is dropped
        # with the §5 malformed-trigger warning, never consumed silently.
        return False
    zone = zone_of(t)
    if zone:
        at = _to_local(at, zone)
    else:
        # Same gap reading as trigger_next — the two must agree about a
        # one-shot whose wall time falls in the local spring-forward gap.
        at = _local_gap_fix(at, None) or at
    return at <= (now or datetime.now())


def next_at(triggers: list[dict], after: datetime | None = None,
            run_baseline: datetime | None = None) -> datetime | None:
    """§4.3 nextAtMs: minimum over enabled triggers, None when nothing is coming.
    `run_baseline` anchors interval triggers (trigger_next)."""
    nxts = [n for t in triggers if t["enabled"] if (n := trigger_next(t, after, run_baseline))]
    return min(nxts) if nxts else None


def is_overdue(triggers: list[dict], baseline: datetime, now: datetime | None = None) -> bool:
    """§4.1 overdue: some enabled cron or interval trigger has had two
    consecutive occurrences pass since its baseline with no run — two, not one,
    so a single legitimately skipped moment (§6 busy-skip, a restart at the
    wrong minute) never flags. The baseline is per trigger: the later of
    `baseline` (the automation's run baseline, local naive like every
    trigger_next time) and the trigger's §4.3 enable stamp, so occurrences that
    passed while it was off never count — a re-enable, or a schedule added to
    an old automation, starts counting from that moment, exactly as the §6
    scheduler fires. For an interval that per-trigger baseline is its §4.3
    anchor, so it is overdue once `anchor + 2 × every` has passed. Cron and
    interval only: one-shots are consumed by the §4.3 spent rule, and
    app-start/message triggers have no schedule."""
    now = now or datetime.now()
    for t in triggers:
        if t.get("kind") not in SCHEDULE_KINDS or not t.get("enabled"):
            continue
        if not run_if_missed(t):
            # §4.1: a schedule that opted out of the §6 wake catch-up chose its
            # misses: a sleeping Mac is the one way a live scheduler misses a
            # moment, and the §6 drop record already shows each one.
            continue
        since = enabled_since(t)  # None for a trigger stored without the stamp
        first = trigger_next(t, after=max(baseline, since) if since else baseline,
                             run_baseline=baseline)
        second = trigger_next(t, after=first, run_baseline=baseline) if first else None
        if second is not None and second < now:
            return True
    return False


def trigger_chip(triggers: list[dict]) -> str:
    if not triggers:
        return "No triggers"
    if len(triggers) == 1:
        return trigger_display(triggers[0])[1]
    return f"{len(triggers)} triggers"
