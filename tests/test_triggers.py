import time
from datetime import datetime, timedelta

import pytest

from autowright.triggers import (
    CronError, cron_display, cron_next, next_at, normalize_triggers,
    parse_cron, time_display, trigger_chip, trigger_next, validate_trigger,
)


def test_cron_daily_rolls_forward():
    now = datetime(2026, 7, 10, 9, 0)  # Friday 9:00
    assert cron_next("0 8 * * *", after=now) == datetime(2026, 7, 11, 8, 0)
    assert cron_next("30 21 * * *", after=now) == datetime(2026, 7, 10, 21, 30)


def test_cron_weekly_dow_sunday_zero():
    now = datetime(2026, 7, 10, 9, 0)  # Friday
    assert cron_next("0 9 * * 1", after=now) == datetime(2026, 7, 13, 9, 0)  # Monday
    assert cron_next("0 21 * * 0", after=now) == datetime(2026, 7, 12, 21, 0)  # Sunday
    # same dow, time already passed → next week
    assert cron_next("0 8 * * 5", after=now) == datetime(2026, 7, 17, 8, 0)  # Friday


def test_cron_lists_ranges_steps():
    now = datetime(2026, 7, 10, 9, 1)
    # every 15 minutes
    assert cron_next("*/15 * * * *", after=now) == datetime(2026, 7, 10, 9, 15)
    # weekday mornings 9-17 hourly
    assert cron_next("0 9-17 * * 1-5", after=now) == datetime(2026, 7, 10, 10, 0)
    # explicit list
    assert cron_next("0 8,20 * * *", after=now) == datetime(2026, 7, 10, 20, 0)


def test_cron_vixie_dom_dow_either_matches():
    # Both restricted: the 15th OR Mondays.
    now = datetime(2026, 7, 10, 9, 0)  # Friday
    assert cron_next("0 8 15 * 1", after=now) == datetime(2026, 7, 13, 8, 0)  # Monday first
    assert cron_next("0 8 15 * 1", after=datetime(2026, 7, 13, 9, 0)) == datetime(2026, 7, 15, 8, 0)


def test_cron_unsatisfiable_returns_none():
    assert cron_next("0 0 30 2 *", after=datetime(2026, 7, 10)) is None


def test_cron_rejects_bad_expressions():
    for expr in ["", "0 8 * *", "60 8 * * *", "0 8 * * 7", "0 8 * * mon", "@daily", "0 8 * * 1-0"]:
        with pytest.raises(CronError):
            parse_cron(expr)


def test_labels():
    assert cron_display("0 8 * * *") == ("Daily at 8:00", "Daily 8:00")
    assert cron_display("0 9 * * 1") == ("Mondays at 9:00", "Mon 9:00")
    assert cron_display("0 21 * * 0")[1] == "Sun 21:00"
    # anything beyond the two simple shapes shows the raw expression
    assert cron_display("*/15 9-17 * * 1-5") == ("*/15 9-17 * * 1-5", "*/15 9-17 * * 1-5")
    assert time_display("2026-07-20T15:00") == ("Once at Jul 20, 3:00 PM", "Once Jul 20 15:00")
    # §4.3: seconds show only when non-zero
    assert time_display("2026-07-20T15:00:15") == (
        "Once at Jul 20, 3:00:15 PM", "Once Jul 20 15:00:15")
    assert time_display("2026-07-20T15:00:00") == ("Once at Jul 20, 3:00 PM", "Once Jul 20 15:00")


def test_trigger_chip_and_next_at():
    t1 = {"id": "1", "kind": "cron", "enabled": True, "expression": "0 8 * * *"}
    t2 = {"id": "2", "kind": "cron", "enabled": True, "expression": "0 2 * * *"}
    assert trigger_chip([]) == "No triggers"
    assert trigger_chip([t1]) == "Daily 8:00"
    assert trigger_chip([t1, t2]) == "2 triggers"
    now = datetime(2026, 7, 10, 9, 0)
    assert next_at([t1, t2], after=now) == datetime(2026, 7, 11, 2, 0)
    assert next_at([{**t1, "enabled": False}, {**t2, "enabled": False}], after=now) is None


def test_time_trigger_validation_and_next():
    future = (datetime.now() + timedelta(days=1)).isoformat(timespec="minutes")
    past = (datetime.now() - timedelta(days=1)).isoformat(timespec="minutes")
    assert validate_trigger({"kind": "time", "at": future}) is None
    assert "future" in validate_trigger({"kind": "time", "at": past})
    assert "timestamp" in validate_trigger({"kind": "time", "at": "not-a-time"})
    t = {"id": "1", "kind": "time", "enabled": True, "at": future}
    assert trigger_next(t) == datetime.fromisoformat(future)
    assert trigger_next(t, after=datetime.fromisoformat(future)) is None  # spent


def test_tz_validation():
    assert validate_trigger({"kind": "cron", "expression": "0 8 * * *", "timezone": "Asia/Tokyo",
                             "source": "spec"}) is None
    assert "unknown timezone" in validate_trigger({"kind": "cron", "expression": "0 8 * * *",
                                                   "timezone": "Mars/Olympus", "source": "spec"})
    assert "unknown timezone" in validate_trigger({"kind": "time", "at": "2099-01-01T00:00", "timezone": 5})
    norm, err = normalize_triggers([{"kind": "cron", "expression": "0 8 * * *", "timezone": "UTC",
                                     "source": "spec"}])
    assert err is None and norm[0]["timezone"] == "UTC"
    norm, err = normalize_triggers([{"kind": "cron", "expression": "0 8 * * *", "source": "spec"}])
    assert err is None and "timezone" not in norm[0]
    # §4.3: `timezone` belongs to cron/time only — a stray one on a message
    # trigger drops at normalize (kept, the loader would drop it on restart)
    norm, err = normalize_triggers([{"kind": "discord", "channel": "42",
                                     "secret": "9b2f4e12-8c3d-4f6a-9e01-2b7c5d8a1f34",
                                     "timezone": "UTC"}])
    assert err is None and "timezone" not in norm[0]
    norm, err = normalize_triggers([{"kind": "imessage", "from": "+15551234567",
                                     "timezone": "UTC"}])
    assert err is None and "timezone" not in norm[0]


def test_tz_cron_next_is_zone_wall_clock():
    from datetime import timezone
    now = datetime(2026, 7, 10, 9, 0)
    # "0 8 * * *" in UTC: next 08:00 UTC after `now` (local), expressed in local naive time.
    got = trigger_next({"id": "1", "kind": "cron", "enabled": True, "expression": "0 8 * * *", "timezone": "UTC"}, after=now)
    now_utc = now.astimezone(timezone.utc).replace(tzinfo=None)
    nxt_utc = now_utc.replace(hour=8, minute=0, second=0, microsecond=0)
    if nxt_utc <= now_utc:
        nxt_utc += timedelta(days=1)
    assert got == nxt_utc.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def test_tz_time_trigger():
    from datetime import timezone
    wall = datetime.now(timezone.utc) + timedelta(hours=2)
    at = wall.replace(tzinfo=None).isoformat(timespec="minutes")
    t = {"id": "1", "kind": "time", "enabled": True, "at": at, "timezone": "UTC"}
    assert validate_trigger(t) is None
    got = trigger_next(t)
    expect = datetime.fromisoformat(at).replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert got == expect
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).replace(tzinfo=None).isoformat(timespec="minutes")
    assert "future" in validate_trigger({"kind": "time", "at": past, "timezone": "UTC"})


def test_tz_labels():
    assert cron_display("0 8 * * *", "Asia/Tokyo") == ("Daily at 8:00 (Tokyo)", "Daily 8:00 (Tokyo)")
    assert cron_display("0 9 * * 1", "America/New_York")[1] == "Mon 9:00 (New York)"
    assert cron_display("*/15 * * * *", "UTC") == ("*/15 * * * * (UTC)", "*/15 * * * * (UTC)")
    assert time_display("2026-07-20T15:00", "Asia/Tokyo") == (
        "Once at Jul 20, 3:00 PM (Tokyo)", "Once Jul 20 15:00 (Tokyo)")


def test_app_start_trigger():
    from autowright.triggers import trigger_display

    assert validate_trigger({"kind": "app_start"}) is None
    norm, err = normalize_triggers([{"kind": "app_start", "enabled": False, "timezone": "UTC"}])
    assert err is None
    assert norm[0]["kind"] == "app_start" and norm[0]["enabled"] is False and norm[0]["id"]
    assert "timezone" not in norm[0] and "expression" not in norm[0] and "at" not in norm[0]
    # §4.3: at most one per automation
    _, err = normalize_triggers([{"kind": "app_start"}, {"kind": "app_start"}])
    assert "one app-start" in err
    t = {"id": "1", "kind": "app_start", "enabled": True}
    assert trigger_next(t) is None  # no computable next occurrence
    assert next_at([t]) is None
    assert trigger_display(t) == ("On app start", "App start")
    assert trigger_chip([t]) == "App start"


def test_reserved_and_unknown_kinds_rejected():
    assert "coming soon" in validate_trigger({"kind": "pubsub"})
    assert "unknown" in validate_trigger({"kind": "webhook"})
    _, err = normalize_triggers([{"kind": "imessage"}])  # no `from` → invalid
    assert err
    # §4.3: cron `source` is required — absent is a validation error, not "spec"
    _, err = normalize_triggers([{"kind": "cron", "expression": "0 8 * * *"}])
    assert err and "source" in err
    norm, err = normalize_triggers([{"kind": "cron", "expression": "0 8 * * *",
                                     "enabled": False, "source": "user"}])
    assert err is None and norm[0]["enabled"] is False and norm[0]["id"]
    assert norm[0]["source"] == "user"


def test_imessage_trigger_validation_and_normalization():
    from autowright.triggers import normalize_triggers, validate_trigger

    # valid: E.164 phone (formatting tolerated) or an email
    assert validate_trigger({"kind": "imessage", "from": "+15551234567"}) is None
    assert validate_trigger({"kind": "imessage", "from": "+1 (555) 123-4567"}) is None
    assert validate_trigger({"kind": "imessage", "from": "Pal@Example.com"}) is None
    # rejected: no country code (could never match a stored handle), spaces in
    # an email, empty, bad pattern
    assert "country code" in validate_trigger({"kind": "imessage", "from": "5551234567"})
    assert "country code" in validate_trigger({"kind": "imessage", "from": "555-123-4567"})
    assert "spaces" in validate_trigger({"kind": "imessage", "from": "a b@c.com"})
    assert validate_trigger({"kind": "imessage", "from": "  "})
    assert "pattern" in validate_trigger(
        {"kind": "imessage", "from": "+15551234567", "pattern": " "})
    # §4.3: obvious phone formatting strips at save; emails pass through
    norm, err = normalize_triggers([
        {"kind": "imessage", "from": "+1 (555) 123-4567", "pattern": " go "},
        {"kind": "imessage", "from": "Pal@Example.com"}])
    assert err is None
    assert (norm[0]["from"], norm[0]["pattern"]) == ("+15551234567", "go")
    assert norm[1]["from"] == "Pal@Example.com"


def test_discord_trigger_validation():
    ok = {"kind": "discord", "channel": "123456789",
          "secret": "9b2f4e12-8c3d-4f6a-9e01-2b7c5d8a1f34"}
    assert validate_trigger(ok) is None
    # channel: numeric snowflake only; secret: a §4.8 secret id (uuid form,
    # §4.3 — a name is no longer a valid reference); pattern nonempty; mention bool
    assert validate_trigger({**ok, "channel": "general"})
    assert validate_trigger({**ok, "channel": ""})
    assert validate_trigger({**ok, "secret": ""})
    assert validate_trigger({**ok, "secret": "DISCORD_BOT_TOKEN"})
    assert validate_trigger({**ok, "secret": "lower case"})
    assert validate_trigger({**ok, "pattern": "  "})
    assert validate_trigger({**ok, "mention": "yes"})
    assert validate_trigger({**ok, "pattern": "deploy", "mention": True}) is None
    # author: optional sender filter, a nonempty list of numeric user ids
    assert validate_trigger({**ok, "author": "9876543210"})   # scalar → API shape is a list
    assert validate_trigger({**ok, "author": []})
    assert validate_trigger({**ok, "author": ["dave"]})
    assert validate_trigger({**ok, "author": ["123", ""]})
    assert validate_trigger({**ok, "author": [123]})
    assert validate_trigger({**ok, "author": ["9876543210"]}) is None
    assert validate_trigger({**ok, "author": ["1", "2"]}) is None


def test_discord_trigger_normalize_and_display():
    sid = "9b2f4e12-8c3d-4f6a-9e01-2b7c5d8a1f34"
    norm, err = normalize_triggers([{"kind": "discord", "channel": " 42 ",
                                     "secret": f" {sid} ", "pattern": " go ",
                                     "mention": True, "author": [" 777 ", "111", "777"]}])
    assert err is None
    t = norm[0]
    # author normalizes trimmed + deduped + sorted (§4.3 merge identity)
    assert (t["channel"], t["secret"], t["pattern"], t["mention"], t["author"]) == \
        ("42", sid, "go", True, ["111", "777"])
    from autowright.triggers import trigger_display

    assert trigger_display(t) == ("Discord · 42 · “go”", "Discord")
    plain, _ = normalize_triggers([{"kind": "discord", "channel": "42", "secret": sid}])
    assert "pattern" not in plain[0] and "mention" not in plain[0] and "author" not in plain[0]
    assert trigger_display(plain[0]) == ("Discord · 42", "Discord")
    # §11: a missing detail field renders "missing" — never "Discord · "
    assert trigger_display({"kind": "discord", "channel": ""}) == ("Discord · missing", "Discord")
    assert trigger_display({"kind": "discord"}) == ("Discord · missing", "Discord")
    assert trigger_display({"kind": "imessage", "from": ""}) == ("iMessage · missing", "iMessage")
    assert trigger_display({"kind": "imessage"}) == ("iMessage · missing", "iMessage")
    # no computable next occurrence (§4.3) — nextAtMs ignores discord
    assert trigger_next(plain[0]) is None
    assert next_at([{**plain[0], "enabled": True}]) is None


def test_specmd_roundtrip():
    from autowright.specmd import blocks_to_md, md_to_blocks

    blocks = [
        {"kind": "h1", "text": "Title"},
        {"kind": "p", "text": "A paragraph of text."},
        {"kind": "h2", "text": "Section"},
        {"kind": "li", "text": "first"},
        {"kind": "li", "text": "second"},
        {"kind": "p", "text": "Closing."},
    ]
    assert md_to_blocks(blocks_to_md(blocks)) == blocks


def test_specmd_numbered_lists_stay_separate():
    """§4.1: adjacent numbered-list lines never merge into one paragraph - the
    list survives the spec.md round trip readable."""
    from autowright.specmd import blocks_to_md, md_to_blocks

    got = md_to_blocks("# T\n\n1. first\n2. second\n3) third\nplain tail\n")
    assert got == [
        {"kind": "h1", "text": "T"},
        {"kind": "p", "text": "1. first"},
        {"kind": "p", "text": "2. second"},
        {"kind": "p", "text": "3) third"},
        {"kind": "p", "text": "plain tail"},
    ]
    assert md_to_blocks(blocks_to_md(got)) == got


# ---------- §4.3 DST behavior (triggers.py is the ONE trigger-math
# implementation — the renderer previews via §19 POST /triggers/preview) ----------

def _utc_str_to_local_naive(s):
    from datetime import timezone

    return (datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
            .replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None))


def test_dst_spring_forward_gap_shifts_by_gap_width():
    """§4.3: 2:30 AM is erased on 2027-03-14 in Los Angeles — the trigger
    still fires, shifted forward by the gap width: the erased wall time read
    with the pre-transition offset lands at 3:30 local (10:30 UTC)."""
    trig = {"kind": "cron", "expression": "30 2 * * *", "timezone": "America/Los_Angeles",
            "enabled": True, "id": "x"}
    after = _utc_str_to_local_naive("2027-03-13T18:00:00Z")
    got = trigger_next(trig, after=after)
    assert got == _utc_str_to_local_naive("2027-03-14T10:30:00Z")


def test_dst_fall_back_ambiguity_fires_once_at_earlier_instant():
    """§4.3: 1:30 AM happens twice on 2026-11-01 in Los Angeles — one firing,
    at the earlier instant (08:30 UTC, PDT side)."""
    trig = {"kind": "cron", "expression": "30 1 * * *", "timezone": "America/Los_Angeles",
            "enabled": True, "id": "x"}
    after = _utc_str_to_local_naive("2026-10-31T18:00:00Z")
    got = trigger_next(trig, after=after)
    assert got == _utc_str_to_local_naive("2026-11-01T08:30:00Z")
    # the occurrence after it is the next day's (PST) — not the repeated 1:30
    assert trigger_next(trig, after=got) == _utc_str_to_local_naive("2026-11-02T09:30:00Z")


def test_dst_fall_back_is_monotonic_against_baseline():
    """Scheduler contract: trigger_next never returns an occurrence at or
    before `after`. The naive fold=0 conversion broke this inside a foreign
    zone's repeated hour (baseline mid-window → the same occurrence re-fired
    every tick, measured 120 executions in 30 minutes)."""
    trig = {"kind": "cron", "expression": "30 1 * * *", "timezone": "America/New_York",
            "enabled": True, "id": "x"}
    # 06:15 UTC = 01:15 EST — inside the second pass of NY's repeated hour.
    after = _utc_str_to_local_naive("2026-11-01T06:15:00Z")
    got = trigger_next(trig, after=after)
    assert got is not None and got > after
    # walking the chain forward stays strictly monotonic through the transition
    seen = []
    cur = _utc_str_to_local_naive("2026-11-01T04:00:00Z")
    for _ in range(4):
        nxt = trigger_next(trig, after=cur)
        assert nxt is not None and nxt > cur
        seen.append(nxt)
        cur = nxt
    assert seen == sorted(seen)


def test_cron_display_dow_edge_parity():
    """§4.3: only a single-digit 0-6 dow humanizes; "7", "07", "00" fall back
    to the raw expression — no exception, no humanizing."""
    for expr in ("0 8 * * 7", "0 8 * * 07", "0 8 * * 00"):
        assert cron_display(expr) == (expr, expr)
    # whitespace-padded expressions fall back trimmed
    assert cron_display("  0 8 1 * *  ") == ("0 8 1 * *", "0 8 1 * *")
    # single-digit 0-6 humanizes
    assert cron_display("0 8 * * 0") == ("Sundays at 8:00", "Sun 8:00")
    assert cron_display("0 8 * * 6") == ("Saturdays at 8:00", "Sat 8:00")


def test_trigger_exec_labels(monkeypatch):
    # §4.5: executions store the trigger's machine kind; the display label is
    # derived at serialization by storage.trigger_label. `menubar` is the one
    # label that names a platform surface, so it follows the §9 per-OS rule.
    from autowright import paths
    from autowright.storage import trigger_label
    monkeypatch.setattr(paths, "current_os", lambda: "macos")

    assert trigger_label("cron") == "Cron"
    assert trigger_label("app_start") == "App start"
    assert trigger_label("time") == "Once"
    assert trigger_label("discord") == "Discord"
    assert trigger_label("manual") == "Manual"
    assert trigger_label("menubar") == "Menu bar"
    assert trigger_label("test") == "Test"
    for token in ("windows", "linux"):
        monkeypatch.setattr(paths, "current_os", lambda token=token: token)
        assert trigger_label("menubar") == "Tray"
        assert trigger_label("manual") == "Manual"


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="POSIX-only tzset")
def test_dst_spring_forward_gap_local_no_timezone():
    """§4.3: the gap rule also applies when the trigger has no `timezone` and
    runs on the system zone - 2:30 AM erased by spring-forward fires at 3:30,
    not at the first tick past 3:00."""
    import os
    import time as _time

    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/Los_Angeles"
    _time.tzset()
    try:
        trig = {"kind": "cron", "expression": "30 2 * * *", "enabled": True, "id": "x"}
        got = trigger_next(trig, after=datetime(2027, 3, 14, 1, 0))
        assert got == datetime(2027, 3, 14, 3, 30)
        # ordinary days are untouched
        assert trigger_next(trig, after=datetime(2027, 3, 15, 1, 0)) == datetime(2027, 3, 15, 2, 30)
        # a one-shot staged into the erased window shifts the same way
        one = {"kind": "time", "at": "2027-03-14T02:30", "enabled": True, "id": "y"}
        assert trigger_next(one, after=datetime(2027, 3, 14, 1, 0)) == datetime(2027, 3, 14, 3, 30)
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        _time.tzset()


def test_cron_trailing_slash_rejected():
    """Backend and renderer cron parsers must agree: "5/" is invalid, not step 1."""
    with pytest.raises(CronError):
        parse_cron("5/ * * * *")
    parse_cron("*/5 * * * *")  # real steps still parse


def test_is_overdue_two_missed_cron_occurrences():
    """§4.1 overdue: two consecutive missed moments flag; one is the grace."""
    from autowright.triggers import is_overdue

    trig = [{"id": "t", "kind": "cron", "enabled": True,
             "expression": "0 8 * * *", "source": "user"}]
    base = datetime(2026, 7, 1, 8, 30)
    # one missed moment (July 2 08:00) — grace, not overdue
    assert not is_overdue(trig, base, datetime(2026, 7, 2, 9, 0))
    # the second moment (July 3 08:00) hasn't passed yet
    assert not is_overdue(trig, base, datetime(2026, 7, 3, 7, 59))
    # two missed moments → overdue
    assert is_overdue(trig, base, datetime(2026, 7, 3, 8, 1))
    # a disabled trigger never counts
    off = [{**trig[0], "enabled": False}]
    assert not is_overdue(off, base, datetime(2026, 8, 1, 0, 0))


def test_is_overdue_counts_from_the_enable_stamp():
    """§4.1/§4.3: the per-trigger baseline is the later of the run baseline and
    the trigger's enable stamp — occurrences that passed while it was off are
    ignored after a re-enable, and misses after the stamp still flag. A trigger
    stored without the stamp keeps the plain baseline."""
    from autowright.triggers import is_overdue

    base = datetime(2026, 7, 1, 8, 30)  # a stale run baseline
    stamped = [{"id": "t", "kind": "cron", "enabled": True, "expression": "0 8 * * *",
                "source": "user",
                # re-enabled July 20, local — a fortnight of 8:00s passed while off
                "enabledAt": datetime(2026, 7, 20, 9, 0).astimezone().isoformat()}]
    assert not is_overdue(stamped, base, datetime(2026, 7, 21, 8, 1))  # one since the stamp
    assert is_overdue(stamped, base, datetime(2026, 7, 22, 8, 1))      # two since the stamp
    # without the stamp the same list is overdue on the stale baseline alone
    legacy = [{k: v for k, v in stamped[0].items() if k != "enabledAt"}]
    assert is_overdue(legacy, base, datetime(2026, 7, 21, 8, 1))
    # an unreadable stamp reads as absent (§5 lenient), never raises
    assert is_overdue([{**stamped[0], "enabledAt": "whenever"}], base,
                      datetime(2026, 7, 21, 8, 1))
    # a stamp older than the run baseline never widens the window
    old = [{**stamped[0], "enabledAt": datetime(2020, 1, 1).astimezone().isoformat()}]
    assert not is_overdue(old, base, datetime(2026, 7, 2, 9, 0))


def test_stamp_enabled_transitions():
    """§4.3 enable stamp: minted on an off-to-on transition and on an entry
    created enabled, carried forward while it stays on, kept while off, and
    never invented for a stored trigger that has none."""
    from autowright.triggers import stamp_enabled

    t = {"id": "t", "kind": "cron", "enabled": False, "expression": "0 8 * * *",
         "source": "user"}
    assert stamp_enabled([t]) == [t]  # off → nothing stamped
    on = stamp_enabled([{**t, "enabled": True}], [t], now_iso="2026-07-20T09:00:00+00:00")
    assert on[0]["enabledAt"] == "2026-07-20T09:00:00+00:00"
    # still on → carried forward, whatever else changed
    same = stamp_enabled([{**t, "enabled": True, "expression": "0 9 * * *"}], on,
                         now_iso="2026-08-01T00:00:00+00:00")
    assert same[0]["enabledAt"] == "2026-07-20T09:00:00+00:00"
    # off keeps the old stamp; back on mints a fresh one
    off = stamp_enabled([t], on, now_iso="2026-08-01T00:00:00+00:00")
    assert off[0]["enabledAt"] == "2026-07-20T09:00:00+00:00"
    again = stamp_enabled([{**t, "enabled": True}], off, now_iso="2026-08-02T00:00:00+00:00")
    assert again[0]["enabledAt"] == "2026-08-02T00:00:00+00:00"
    # a stored stampless trigger that stays on is never healed
    legacy = stamp_enabled([{**t, "enabled": True}], [{**t, "enabled": True}],
                           now_iso="2026-08-02T00:00:00+00:00")
    assert "enabledAt" not in legacy[0]
    # a client-sent stamp is discarded
    faked = stamp_enabled([{**t, "enabled": True, "enabledAt": "2999-01-01T00:00:00+00:00"}],
                          [{**t, "enabled": True}], now_iso="2026-08-02T00:00:00+00:00")
    assert "enabledAt" not in faked[0]


def test_is_overdue_ignores_unscheduled_kinds():
    """§4.1: one-shots are the §4.3 spent rule's job; message/app-start
    triggers have no schedule — none of them can be overdue."""
    from autowright.triggers import is_overdue

    base = datetime(2026, 7, 1, 8, 30)
    far = datetime(2026, 12, 1, 0, 0)
    trigs = [{"id": "a", "kind": "time", "enabled": True, "at": "2026-07-02T08:00"},
             {"id": "b", "kind": "app_start", "enabled": True},
             {"id": "c", "kind": "discord", "enabled": True, "channel": "1",
              "secret": "9b2f4e12-8c3d-4f6a-9e01-2b7c5d8a1f34"},
             {"id": "d", "kind": "imessage", "enabled": True, "from": "x@y.z"}]
    assert not is_overdue(trigs, base, far)


def test_run_if_missed_validation_and_normalization():
    """§4.3 `runIfMissed`: cron/time only: a non-boolean is a validation
    error, the opt-out stores only when false (true is the absent pre-field
    shape, §21), and every other kind ignores the key."""
    from autowright.triggers import RUN_IF_MISSED_ERROR, run_if_missed

    future = (datetime.now() + timedelta(days=1)).isoformat(timespec="minutes")
    cron = {"kind": "cron", "expression": "0 8 * * *", "source": "user"}
    once = {"kind": "time", "at": future}
    for base in (cron, once):
        assert validate_trigger({**base, "runIfMissed": False}) is None
        assert validate_trigger({**base, "runIfMissed": True}) is None
        assert validate_trigger({**base, "runIfMissed": "no"}) == RUN_IF_MISSED_ERROR
        assert validate_trigger({**base, "runIfMissed": None}) == RUN_IF_MISSED_ERROR

    norm, err = normalize_triggers([{**cron, "runIfMissed": False},
                                    {**once, "runIfMissed": False},
                                    {**cron, "runIfMissed": True},
                                    dict(once)])
    assert err is None
    # stored only when false: true is never written, and reads back as true
    assert [t.get("runIfMissed", "absent") for t in norm] == [False, False, "absent", "absent"]
    assert [run_if_missed(t) for t in norm] == [False, False, True, True]

    # every other kind ignores the key: never validated, never stored
    assert validate_trigger({"kind": "app_start", "runIfMissed": "no"}) is None
    norm, err = normalize_triggers([
        {"kind": "app_start", "runIfMissed": False},
        {"kind": "discord", "channel": "42",
         "secret": "9b2f4e12-8c3d-4f6a-9e01-2b7c5d8a1f34", "runIfMissed": False}])
    assert err is None
    assert all("runIfMissed" not in t for t in norm)


def test_only_a_parsable_past_at_counts_as_spent():
    """§4.3: a one-shot is consumed when its moment passed — but only when the
    stored `at` actually reads as a time. An unreadable one (a `datetime` from
    unquoted YAML, a number, a missing key) is malformed, not spent, so the
    §5 load drops it with a warning instead of silently consuming it."""
    from autowright.triggers import time_elapsed

    past = (datetime.now() - timedelta(days=1)).isoformat(timespec="minutes")
    future = (datetime.now() + timedelta(days=1)).isoformat(timespec="minutes")
    assert time_elapsed({"kind": "time", "at": past}) is True
    assert time_elapsed({"kind": "time", "at": future}) is False

    for bad in (datetime.now() - timedelta(days=1), 20260101, "yesterday", None, ""):
        assert time_elapsed({"kind": "time", "at": bad}) is False
    assert time_elapsed({"kind": "time"}) is False
    assert time_elapsed({"kind": "cron", "expression": "0 8 * * *"}) is False
