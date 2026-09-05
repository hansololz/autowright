"""Seed / demo data (§16) — the prototype's four automations, two secrets, and
twelve executions covering every terminal status including skipped. Test
fixture data only; step code is illustrative (drafted-by-an-agent style), not
guaranteed executable.

Lives in tests/ — the shipped app has no seed path and starts empty.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from autowright import keychain
from autowright.storage import Store, new_id


# §4.1/§4.8 fixed fixture ids — step code references secrets by id, so the
# seeded records and the code strings below must agree.
SMTP_PASSWORD_ID = "51111111-1111-4111-8111-111111111111"
VAULT_DRIVE_KEY_ID = "52222222-2222-4222-8222-222222222222"


def _mk_ver(desc, params, steps, spec, instr=None, note=None):
    return {"description": desc, "params": params, "steps": steps, "spec": spec,
            "instructions": instr, "note": note}


def seed(store: Store) -> None:
    if store.autos:
        return

    now = datetime.now()

    # ---------- secrets (values → Keychain; ids + names + descs → secrets.yaml) ----------
    # Fixed ids: the seeded step code references secrets by id (§4.1), so the
    # ids must be stable across the fixture's code strings below.
    secret_ids = {"SMTP_PASSWORD": SMTP_PASSWORD_ID, "VAULT_DRIVE_KEY": VAULT_DRIVE_KEY_ID}
    for name, value, desc in [
            ("SMTP_PASSWORD", "mail-app-2291-kx7f", "App password for outgoing mail"),
            ("VAULT_DRIVE_KEY", "bk-2f91-aa07-51d3", "Encryption key for the backup drive")]:
        keychain.set_secret(secret_ids[name], value)  # §4.8: Keychain keyed by id
        if not any(s["name"] == name for s in store.secrets):
            store.secrets.append({"id": secret_ids[name], "name": name, "description": desc})
    store.save_secrets()

    # ---------- agents ----------
    if not store.agents:
        store.agents = [{"id": "claude", "name": None, "harness": "Claude Code",
                         "mode": "default", "model": None}]
        store.default_agent_id = "claude"  # §4.7: single pointer, never a per-record flag
        store.save_agents()
    agent_id = store.default_agent_id or store.agents[0]["id"]

    # ---------- Track manga chapters ----------
    manga_params = [
        {"name": "manga_list", "kind": "list", "label": "Manga list",
         "help": "One link per line — the manga pages to watch.", "validate": True, "default": []},
        {"name": "notify_only_on_changes", "kind": "toggle", "label": "Notify only on changes",
         "help": "Skip the morning notification when nothing is new.", "default": True},
        {"name": "chapters_kept_in_history", "kind": "number", "label": "Chapters kept in history",
         "help": "How many past chapters to remember per manga.", "min": 1, "default": 5},
        {"name": "notification_title", "kind": "text", "label": "Notification title",
         "help": "Shown at the top of the notification. Uses the automation name if empty.",
         "placeholder": "Track manga chapters", "default": ""},
        {"name": "display_names", "kind": "kv", "label": "Display names",
         "help": "Show a shorter name for long titles in the result table.", "default": []},
    ]
    manga_steps = [
        {"file": "01-read-your-manga-list.py", "name": "Read your manga list",
         "description": "Loads the list, checks each line is a real link, and skips ones that aren't.",
         "code": 'import json\n\nfrom autowright import log, params\n\n'
                 'lines = [l.strip() for l in params["manga_list"] if l.strip()]\n'
                 'links = [l for l in lines if l.startswith("http")]\n'
                 'skipped = [l for l in lines if not l.startswith("http")]\n'
                 'if skipped:\n    log.warn(f"{len(skipped)} line(s) skipped — not links")\n'
                 'json.dump(links, open("links.json", "w"))  # workspace hands step 2 the list\n'},
        {"file": "02-check-each-site-for-new-chapters.py", "name": "Check each site for new chapters",
         "description": "Visits each manga's page and has an agent read off the newest chapter number and title.",
         "agent": True,
         "why": "Manga sites all lay out their pages differently — plain code can't reliably find the newest chapter in arbitrary HTML. The agent reads each page and returns just the chapter info.",
         "code": 'import json\n\nfrom autowright import agent, fetch_page\n\n'
                 'links = json.load(open("links.json"))\nfound = []\n'
                 'for url in links:\n    page = fetch_page(url)                     # plain HTTP GET\n'
                 '    latest = agent.read(page[:5000],           # sites all differ — the agent\n'
                 '        "newest chapter: number, title, date") # reads the page like a person\n'
                 '    found.append({"url": url, "latest": latest})\n'
                 'json.dump(found, open("found.json", "w"))\n'},
        {"file": "03-compare-with-memory.py", "name": "Compare with memory",
         "description": "Looks at the last chapter seen for each manga to decide what counts as new.",
         "code": 'import json\n\nfrom autowright import memory\n\n'
                 'found = json.load(open("found.json"))\n'
                 'last_seen = memory.load("last_seen", {})\nfor f in found:\n'
                 '    last = last_seen.get(f["url"])\n'
                 '    f["is_new"] = last is not None and f["latest"] != last\n'
                 '    last_seen[f["url"]] = f["latest"]  # remembered for next execution\n'
                 'memory.save("last_seen", last_seen)\n'
                 'json.dump(found, open("found.json", "w"))\n'},
        {"file": "04-notify-and-build-the-result.py", "name": "Notify and build the result",
         "description": "Sends the notification (only on changes) and builds the morning table.",
         "code": 'import json\n\nfrom autowright import notify, params, result\n\n'
                 'found = json.load(open("found.json"))\n'
                 'fresh = [f for f in found if f["is_new"]]\n'
                 'if fresh or not params["notify_only_on_changes"]:\n'
                 '    notify(f"{len(fresh)} new chapters")\n'
                 'result.status("changes" if fresh else "ok")\n'
                 'result.chip(f"{len(fresh)} new chapters" if fresh else "No new chapters")\n'
                 'rows = "\\n".join(f"| {f[\'url\']} | {f[\'latest\']} | {\'NEW\' if f[\'is_new\'] else \'—\'} |" for f in found)\n'
                 '(result.path / "result.md").write_text("| Manga | Latest chapter | New |\\n|---|---|---|\\n" + rows)\n'},
    ]
    manga_spec = [
        {"kind": "h1", "text": "Track manga chapters"},
        {"kind": "p", "text": "Checks each manga in your list every morning and tells you when new chapters are out."},
        {"kind": "h2", "text": "Schedule"},
        {"kind": "p", "text": "Every day at 8:00."},
        {"kind": "h2", "text": "What it does"},
        {"kind": "li", "text": "Reads your manga list and skips lines that aren't links."},
        {"kind": "li", "text": "Visits each manga's page and finds the newest chapter."},
        {"kind": "li", "text": "Compares with the last chapter it saw for each manga."},
        {"kind": "li", "text": "Notifies you and builds a table of what's new."},
        {"kind": "h2", "text": "Settings"},
        {"kind": "li", "text": "Manga list — the pages to watch, one per line."},
        {"kind": "li", "text": "Notify only on changes — skip the notification when nothing is new."},
        {"kind": "h2", "text": "Change (v3)"},
        {"kind": "p", "text": "Added display names so long titles stay readable in the table."},
    ]
    manga_instr = ("Prefer Python for scripts.\nNever delete anything — move files to the Trash instead.\n"
                   "Never pass a secret as the input for an agent.\nKeep it to one notification per execution.")
    manga = store.create_automation(
        _mk_ver("Checks the manga you follow every morning and tells you when new chapters are out.",
                manga_params, manga_steps, manga_spec, instr=manga_instr, note="Created"),
        "Track manga chapters", agent_id, triggers=[{"id": new_id(), "kind": "cron", "source": "spec", "enabled": True, "expression": "0 8 * * *"}])
    # older versions v2 (v1 base), then current becomes v3
    v2_spec = [b for b in manga_spec if not (b["kind"] == "h2" and b["text"].startswith("Change"))
               and b["text"] != "Added display names so long titles stay readable in the table."]
    store.save_new_version(manga, _mk_ver(manga["description"], manga_params, manga_steps,
                                          v2_spec + [{"kind": "h2", "text": "Change (v2)"},
                                                     {"kind": "p", "text": "The table now links straight to the newest chapter."}],
                                          instr=manga_instr,
                                          note="Skip list lines that aren't links instead of failing."))
    store.save_new_version(manga, _mk_ver(manga["description"], manga_params, manga_steps,
                                          manga_spec, instr=manga_instr,
                                          note="Added display names so long titles stay readable in the table."))
    store.patch_automation(manga, {"paramValues": {
        "manga_list": ["https://mangaplus.shueisha.co.jp/titles/100020",
                       "https://comikey.com/comics/kagurabachi",
                       "https://mangadex.org/title/vinland-saga",
                       "https://mangadex.org/title/berserk",
                       "one punch man new site??",
                       "https://mangadex.org/title/dandadan",
                       "https://mangadex.org/title/frieren"],
        "notify_only_on_changes": True,
        "chapters_kept_in_history": 5,
        "notification_title": "",
        "display_names": [{"key": "mangadex.org/title/frieren", "value": "Frieren"},
                          {"key": "comikey.com/comics/kagurabachi", "value": "Kagurabachi"}],
    }})
    (store.auto_dir(manga) / "memory").mkdir(exist_ok=True)
    (store.auto_dir(manga) / "memory" / "last_seen.yaml").write_text(
        "https://mangaplus.shueisha.co.jp/titles/100020: 'Ch. 1145'\n", encoding="utf-8")

    # ---------- Nightly folder backup ----------
    backup_steps = [
        {"file": "01-find-files-changed-since-last-night.py", "name": "Find files changed since last night",
         "description": "Compares file dates against the last execution.",
         "code": 'import json, os\n\nfrom autowright import log, memory, params\n\n'
                 'last = memory.load("last_run_at", 0)\nchanged = []\n'
                 'for root, _, files in os.walk(os.path.expanduser(params["folder_to_back_up"])):\n'
                 '    if params["skip_node_modules_folders"] and "node_modules" in root: continue\n'
                 '    for f in files:\n        p = os.path.join(root, f)\n'
                 '        if os.path.getmtime(p) > last: changed.append(p)\n'
                 'log(f"{len(changed)} files changed")\n'
                 'json.dump(changed, open("changed.json", "w"))\n'},
        {"file": "02-copy-them-to-the-backup-drive.py", "name": "Copy them to the backup drive",
         "description": "Unlocks the Vault drive with its key from the Keychain, then copies with checksums so a bad copy is caught immediately.",
         "code": 'import json, os, shutil\n\nfrom autowright import log, params, secrets\n\n'
                 'changed = json.load(open("changed.json"))\n'
                 f'key = secrets["{VAULT_DRIVE_KEY_ID}"]  # VAULT_DRIVE_KEY — never logged\n'
                 'dest = params["backup_destination"]\n'
                 'if not os.path.isdir(dest):\n'
                 '    raise RuntimeError(f"backup destination {dest} isn\'t mounted")\n'
                 'for f in changed:\n'
                 '    shutil.copy2(f, os.path.join(dest, os.path.basename(f)))\n'
                 'log(f"{len(changed)} of {len(changed)} copied · checksums ok")\n'},
        {"file": "03-prune-old-copies.py", "name": "Prune old copies",
         "description": "Keeps the newest N nightly copies and removes the rest.",
         "code": 'import time\n\nfrom autowright import log, memory, params, result\n\n'
                 'keep = params["copies_to_keep"]\nlog(f"keeping the newest {keep} copies")\n'
                 'memory.save("last_run_at", time.time())\n'
                 'result.status("ok")\nresult.chip("All good")\n'
                 '(result.path / "result.md").write_text("Projects is fully backed up to the Vault drive.")\n'},
    ]
    backup_params = [
        {"name": "folder_to_back_up", "kind": "text", "label": "Folder to back up",
         "help": "Everything inside is watched for changes.", "default": "~/Projects"},
        {"name": "backup_destination", "kind": "text", "label": "Backup destination",
         "help": "Where the copies go.", "default": "/Volumes/Vault/Backups"},
        {"name": "copies_to_keep", "kind": "number", "label": "Copies to keep",
         "help": "Older nightly copies are pruned past this count.", "min": 1, "default": 7},
        {"name": "skip_node_modules_folders", "kind": "toggle", "label": "Skip node_modules folders",
         "help": "Saves a lot of space if you write code.", "default": True},
    ]
    backup_spec = [
        {"kind": "h1", "text": "Nightly folder backup"},
        {"kind": "p", "text": "Copies changed files from Projects to the Vault drive every night at 2:00, keeping the last 7 copies."},
        {"kind": "h2", "text": "Change (v2)"},
        {"kind": "p", "text": "Copies are now verified with checksums."},
    ]
    backup = store.create_automation(
        _mk_ver("Copies changed files from Projects to the backup drive every night.",
                backup_params, backup_steps, backup_spec[:2]),
        "Nightly folder backup", agent_id, triggers=[{"id": new_id(), "kind": "cron", "source": "spec", "enabled": True, "expression": "0 2 * * *"}])
    store.save_new_version(backup, _mk_ver(backup["description"], backup_params, backup_steps,
                                           backup_spec, note="Copies are now verified with checksums."))
    store.patch_automation(backup, {"allowedSecrets": [VAULT_DRIVE_KEY_ID]})

    # ---------- Weekly report email ----------
    report_steps = [
        {"file": "01-gather-the-weeks-numbers.py", "name": "Gather the week's numbers",
         "description": "Reads the four tracking sheets.",
         "code": 'import json\n\nfrom autowright import log, memory\n\n'
                 'rows = memory.load("sources", [])  # 4 sources\n'
                 'log(f"{len(rows) or 4} sources read · 28 rows")\n'
                 'json.dump(rows, open("rows.json", "w"))\n'},
        {"file": "02-write-the-summary.py", "name": "Write the summary",
         "description": "Has an agent turn the numbers into a short readable summary.",
         "agent": True,
         "why": "Writing readable prose from raw numbers is judgment, not rules — the agent drafts the summary from the week's rows. The gathering and sending around it stay plain code.",
         "code": 'import json\n\nfrom autowright import agent\n\n'
                 'rows = json.load(open("rows.json"))\n'
                 'summary = agent.write(rows,\n    "3–4 sentences — what changed this week and why it matters")\n'
                 'open("summary.txt", "w").write(summary)\n'},
        {"file": "03-send-the-email.py", "name": "Send the email",
         "description": "Sends via your mail account. The password comes from the Keychain.",
         "code": 'import smtplib\n\nfrom autowright import log, result, secrets\n\n'
                 'summary = open("summary.txt").read()\n'
                 f'password = secrets["{SMTP_PASSWORD_ID}"]  # SMTP_PASSWORD — never logged\n'
                 'log("connecting to smtp.fastmail.com…")\n'
                 'with smtplib.SMTP("smtp.fastmail.com", 587, timeout=15) as s:\n'
                 '    s.starttls()\n    s.login("me", password)\n'
                 'result.status("ok")\nresult.chip("Email sent")\n'},
        {"file": "04-record-the-send.py", "name": "Record the send",
         "description": "Notes what was sent, for next week's comparison.",
         "code": 'import json, time\n\nfrom autowright import memory\n\n'
                 'rows = json.load(open("rows.json"))\n'
                 'memory.save("last_sent", {"at": time.time(), "rows": len(rows)})\n'},
    ]
    report_params = [
        {"name": "recipients", "kind": "list", "label": "Recipients",
         "help": "One address per line.", "validate": False, "default": []},
        {"name": "subject_line", "kind": "text", "label": "Subject line",
         "help": "The week's dates are added automatically.", "default": "Weekly numbers"},
        {"name": "attach_the_spreadsheet", "kind": "toggle", "label": "Attach the spreadsheet",
         "help": "Includes the raw numbers as a file.", "default": True},
    ]
    report_spec = [
        {"kind": "h1", "text": "Weekly report email"},
        {"kind": "p", "text": "Every Monday at 9:00, gathers the week's numbers, writes a short summary and emails it to the team."},
        {"kind": "h2", "text": "Change (v5)"},
        {"kind": "p", "text": "The spreadsheet attachment is now optional."},
    ]
    report = store.create_automation(
        _mk_ver("Gathers the week's numbers and emails the summary every Monday morning.",
                report_params, report_steps, report_spec[:2]),
        "Weekly report email", agent_id, triggers=[{"id": new_id(), "kind": "cron", "source": "spec", "enabled": True, "expression": "0 9 * * 1"}])
    for note in ["Summary capped at roughly 200 words.",
                 "Added week-over-week comparison to the summary.",
                 "Send to the team alias instead of individual addresses.",
                 "The spreadsheet attachment is now optional."]:
        store.save_new_version(report, _mk_ver(report["description"], report_params,
                                               report_steps, report_spec, note=note))
    store.patch_automation(report, {
        "allowedSecrets": [SMTP_PASSWORD_ID],
        "paramValues": {"recipients": ["team@northbeam.studio", "sam@northbeam.studio", "priya@northbeam.studio"],
                        "subject_line": "Weekly numbers", "attach_the_spreadsheet": True},
    })

    # ---------- Clean screenshots folder ----------
    shots_steps = [
        {"file": "01-find-screenshots-on-the-desktop.py", "name": "Find screenshots on the Desktop",
         "description": "Matches the files macOS names “Screenshot …”.",
         "code": 'import json, os, re\n\nfrom autowright import log\n\n'
                 'desktop = os.path.expanduser("~/Desktop")\n'
                 'shots = [f for f in os.listdir(desktop) if re.match(r"^Screenshot ", f)]\n'
                 'log(f"{len(shots)} screenshots found")\n'
                 'json.dump(shots, open("shots.json", "w"))\n'},
        {"file": "02-file-them-into-monthly-folders.py", "name": "File them into monthly folders",
         "description": "Creates a folder per month and moves them in.",
         "code": 'import json, os, shutil, datetime\n\nfrom autowright import result\n\n'
                 'desktop = os.path.expanduser("~/Desktop")\n'
                 'shots = json.load(open("shots.json"))\nfor s in shots:\n'
                 '    p = os.path.join(desktop, s)\n'
                 '    month = datetime.date.fromtimestamp(os.path.getctime(p)).strftime("%Y-%m")\n'
                 '    dest = os.path.join(desktop, month)\n    os.makedirs(dest, exist_ok=True)\n'
                 '    shutil.move(p, dest)\n'
                 'result.status("ok")\nresult.chip("All good")\n'
                 '(result.path / "result.md").write_text(f"The desktop is clean. {len(shots)} screenshots filed.")\n'},
    ]
    shots = store.create_automation(
        _mk_ver("Files desktop screenshots into monthly folders every Sunday night.",
                [{"name": "also_clean_the_downloads_folder", "kind": "toggle",
                  "label": "Also clean the Downloads folder",
                  "help": "Files loose screenshots from Downloads too.", "default": False}],
                shots_steps,
                [{"kind": "h1", "text": "Clean screenshots folder"},
                 {"kind": "p", "text": "Every Sunday night, files desktop screenshots into monthly folders."}]),
        "Clean screenshots folder", agent_id, triggers=[{"id": new_id(), "kind": "cron", "source": "spec", "enabled": True, "expression": "0 21 * * 0"}])

    # ---------- executions (12, every terminal status incl. skipped) ----------
    manga_result = {"status": "changes", "chip": "2 new chapters"}
    # §16: the manga table is a markdown table in result.md (READ column included).
    manga_result_md = "\n".join([
        "| Manga | Latest chapter | Updated | New | Read |",
        "|---|---|---|---|---|",
        "| [One Piece](https://mangaplus.shueisha.co.jp/titles/100020) | Ch. 1145 · “The Weight of a Promise” | 2h ago | **NEW** | Ch. 1143 |",
        "| [Frieren: Beyond Journey’s End](https://mangadex.org/title/frieren) | Ch. 142 · “The Golden Land” | 5h ago | **NEW** | Ch. 141 |",
        "| [Dandadan](https://mangadex.org/title/dandadan) | Ch. 189 | 2d ago | — | Ch. 189 |",
        "| [Kagurabachi](https://comikey.com/comics/kagurabachi) | Ch. 94 | 4d ago | — | Ch. 92 |",
        "| [Vinland Saga](https://mangadex.org/title/vinland-saga) | Ch. 218 | 6d ago | — | Ch. 218 |",
        "| [Berserk](https://mangadex.org/title/berserk) | Ch. 379 | 3w ago | — | Ch. 378 |",
    ])
    manga_logs = [
        ("step", 1),
        ("out", "7 lines · 6 valid links · 1 skipped (not a link)"),
        ("wrn", "line 5 isn’t a link — “one punch man new site??”"),
        ("step", 2),
        ("out", "mangaplus.shueisha.co.jp · One Piece — Ch. 1145 “The Weight of a Promise”"),
        ("out", "comikey.com · Kagurabachi — Ch. 94"),
        ("out", "mangadex.org · Vinland Saga — Ch. 218"),
        ("out", "mangadex.org · Berserk — Ch. 379"),
        ("out", "mangadex.org · Dandadan — Ch. 189"),
        ("out", "mangadex.org · Frieren — Ch. 142 “The Golden Land”"),
        ("step", 3),
        ("out", "One Piece: 1144 → 1145 · new"),
        ("out", "Frieren: 141 → 142 · new"),
        ("out", "4 manga unchanged"),
        ("step", 4),
        ("out", "notification sent — “2 new chapters”"),
        ("out", "result saved · execution finished in 24.8s"),
    ]

    def _step_file(i, name):
        return f"{i + 1:02d}-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}.py"

    def put_exec(auto, ver, status, trigger, started, duration_ms, steps, logs, result=None,
                 note=None, redacted=None, files=None):
        started_iso = started.astimezone(timezone.utc).isoformat()  # §5 canonical form
        # Step entries are (name, status, dur) — one attempt unless queued — or
        # (name, status, dur, [(att_status, att_dur), …]) for retried steps.
        step_dicts = []
        for i, entry in enumerate(steps):
            name, st, d = entry[0], entry[1], entry[2]
            att_spec = entry[3] if len(entry) > 3 else ([(st, d)] if st != "queued" else [])
            step_dicts.append({
                "name": name, "file": _step_file(i, name), "agent": False,
                "status": st, "duration_ms": d,
                "attempts": [{"number": j + 1, "status": a_st, "started_at": started_iso,
                              "duration_ms": a_d} for j, (a_st, a_d) in enumerate(att_spec)]})
        label2kind = {"Manual": "manual", "Menu bar": "menubar", "Cron": "cron",
                      "Once": "time", "App start": "app_start", "Discord": "discord",
                      "iMessage": "imessage", "Test": "test"}
        h = store.create_execution(auto, "version", int(ver.lstrip("v")),
                                   label2kind[trigger], step_dicts,
                                   note=note, status="executing")
        h["started_at"] = started_iso
        h["status"] = status
        h["duration_ms"] = duration_ms
        h["redacted_secrets"] = redacted or []
        if status in ("succeeded", "failed", "cancelled", "interrupted", "skipped"):
            h["finished_at"] = (started + timedelta(milliseconds=duration_ms or 0)).astimezone(timezone.utc).isoformat()
        # Log lines route by file (§5 logs/): a ("step", N) directive switches
        # to that step's latest attempt's file and is never written itself (§7:
        # an attempt log carries no opener line); lines before any directive
        # are execution-level (execution.ndjson).
        cur = None
        seqs: dict[str, int] = {}
        for k, text in logs:
            if k == "step":
                si = int(text) - 1
                assert 0 <= si < len(step_dicts), (si, step_dicts)
                sd = step_dicts[si]
                cur = store.log_name(sd["file"], si, max(1, len(sd["attempts"])))
                continue
            name = cur or store.EXEC_LOG
            seqs[name] = seqs.get(name, 0) + 1
            # §5: stored lines are {ts, k, seq, text} — `t` is derived at read.
            store.append_log_line(h["id"], name, {"timestamp": started_iso, "kind": k,
                                                  "sequence": seqs[name], "text": text})
        if result:
            # chip + status live on the execution header
            h["chip"] = result.get("chip")
            h["chip_status"] = result.get("status") if result.get("chip") else None
        for name, content in (files or {}).items():
            (store.exec_dir(h["id"]) / "result" / name).write_text(content, encoding="utf-8")
        store.update_execution(h)
        return h

    today8 = now.replace(hour=8, minute=1, second=0, microsecond=0)
    if today8 > now:
        today8 -= timedelta(days=1)
    today2 = now.replace(hour=2, minute=3, second=0, microsecond=0)
    if today2 > now:
        today2 -= timedelta(days=1)
    monday9 = now.replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=(now.weekday()) % 7)
    if monday9 > now:
        monday9 -= timedelta(days=7)

    manga_steps_ok = [("Read your manga list", "succeeded", 400),
                      ("Check each site for new chapters", "succeeded", 19600),
                      ("Compare with memory", "succeeded", 300),
                      ("Notify and build the result", "succeeded", 1100)]
    put_exec(manga, "v3", "succeeded", "Cron", today8, 24800, manga_steps_ok, manga_logs, manga_result,
             files={"result.md": manga_result_md})
    put_exec(backup, "v2", "succeeded", "Cron", today2, 41200,
             [("Find files changed since last night", "succeeded", 3900),
              ("Copy them to the backup drive", "succeeded", 35000),
              ("Prune old copies", "succeeded", 2300)],
             [("step", 1),
              ("out", "142 files changed · 1.8 GB"),
              ("step", 2),
              ("out", "142 of 142 copied · checksums ok"),
              ("step", 3),
              ("out", "removed the copy from Jun 30 · 7 kept")],
             {"status": "ok", "chip": "All good"},
             files={"result.md": "Projects is fully backed up to the Vault drive. "
                                 "142 files copied (1.8 GB, 41 s). Nothing unusual last night."})
    put_exec(report, "v5", "failed", "Cron", monday9, 12400,
             [("Gather the week’s numbers", "succeeded", 5800),
              ("Write the summary", "succeeded", 3100),
              ("Send the email", "failed", 3500),
              ("Record the send", "queued", None)],
             [("step", 1),
              ("out", "4 sources read · 28 rows"),
              ("step", 2),
              ("out", "summary drafted · 214 words"),
              ("step", 3),
              ("out", "connecting to smtp.fastmail.com…"),
              ("err", "sign-in failed — the server rejected the password (535)"),
              ("err", "the SMTP_PASSWORD secret may be out of date"),
              ("sys", "execution failed at step 3 — nothing was sent")],
             {"status": "attention", "chip": "Needs attention"},
             files={"result.md": "## What happened\n\n"
                                 "Monday’s execution couldn’t sign in to the mail server, so no email went out.\n\n"
                                 "## Next steps\n\n"
                                 "- Update the SMTP_PASSWORD secret — the server rejected the current one.\n"
                                 "- Execute it again — the email goes out as soon as an execution succeeds.\n"},
             redacted=["SMTP_PASSWORD"])
    put_exec(manga, "v3", "succeeded", "Menu bar", today8 - timedelta(days=1, minutes=-13), 26100,
             manga_steps_ok, manga_logs,
             {"status": "changes", "chip": "1 new chapter"},
             files={"result.md": "## New chapters\n\n- One Piece — Ch. 1144\n\n6 manga checked · 5 unchanged.\n"})
    # §4.6: a queue entry cancelled before its turn finishes `skipped`, never
    # `cancelled` — it never ran; the note says it was cancelled.
    put_exec(backup, "v2", "skipped", "Cron", today2 - timedelta(days=1), 0, [], [],
             note="previous execution still in progress")
    put_exec(manga, "v2", "succeeded", "Cron", today8 - timedelta(days=5), 22900,
             [("Read your manga list", "succeeded", 400),
              ("Check each site for new chapters", "skipped", 20100),
              ("Compare with memory", "succeeded", 300),
              ("Notify and build the result", "succeeded", 900)],
             [("step", 2),
              ("wrn", "mangadex.org didn’t respond after 3 tries — skipped"),
              ("out", "5 of 6 manga checked")],
             {"status": "attention", "chip": "5 of 6 checked"},
             files={"result.md": "No new chapters among the 5 manga that were checked.\n\n"
                                 "- mangadex.org didn’t respond after 3 tries — skipped this execution\n"})
    put_exec(manga, "v2", "skipped", "Cron", today8 - timedelta(days=6), 0, [], [],
             note="previous execution still in progress")
    put_exec(manga, "v2", "cancelled", "Manual", now.replace(hour=15, minute=12) - timedelta(days=7), 8400,
             [("Read your manga list", "succeeded", 400),
              ("Check each site for new chapters", "cancelled", 7800),
              ("Compare with memory", "queued", None),
              ("Notify and build the result", "queued", None)],
             [("step", 2),
              ("sys", "execution cancelled by you — nothing else will happen")])
    put_exec(shots, "v1", "interrupted", "Cron", now.replace(hour=21, minute=0) - timedelta(days=11), 3100,
             [("Find screenshots on the Desktop", "interrupted", 3100),
              ("File them into monthly folders", "queued", None)],
             [("wrn", "the Mac went to sleep — the execution will resume next Sunday")],
             note="Mac went to sleep")
    put_exec(report, "v4", "succeeded", "Cron", monday9 - timedelta(days=7), 18300,
             [("Gather the week’s numbers", "succeeded", 6000),
              ("Write the summary", "succeeded", 3400),
              ("Send the email", "succeeded", 7700),
              ("Record the send", "succeeded", 400)],
             [("out", "email sent to 3 recipients")],
             {"status": "ok", "chip": "Email sent"},
             files={"result.md": "The weekly summary went out to the team at 9:00 — 3 recipients, 198 words."},
             redacted=["SMTP_PASSWORD"])
    put_exec(shots, "v1", "succeeded", "Cron", now.replace(hour=21, minute=0) - timedelta(days=4), 5200,
             [("Find screenshots on the Desktop", "succeeded", 1100),
              ("File them into monthly folders", "succeeded", 4100)],
             [("step", 1),
              ("out", "38 screenshots found"),
              ("step", 2),
              ("out", "38 filed into 2026-06")],
             {"status": "ok", "chip": "All good"},
             files={"result.md": "The desktop is clean. 38 screenshots went into 2026-06."})
    # §16: a failed execution retried in place — the failing step carries two attempts
    put_exec(report, "v5", "failed", "Manual", monday9 + timedelta(hours=2), 16600,
             [("Gather the week’s numbers", "succeeded", 5800),
              ("Write the summary", "succeeded", 3100),
              ("Send the email", "failed", 4200, [("failed", 3500), ("failed", 4200)]),
              ("Record the send", "queued", None)],
             [("sys", "retrying from step 3 — attempt 2"),
              ("step", 3),
              ("err", "sign-in failed — the server rejected the password (535)")],
             redacted=["SMTP_PASSWORD"])
    store._refresh_exec_derived()
