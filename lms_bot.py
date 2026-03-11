import re
import requests
import json
import os

LMS_URL = "https://lmssithyd.siu.edu.in"

USERNAME        = os.getenv("LMS_USER")
PASSWORD        = os.getenv("LMS_PASS")
DISCORD_WEBHOOK = os.getenv("WEBHOOK_URL")

# Set to "true" by the workflow when triggered manually
MANUAL_RUN = os.getenv("MANUAL_RUN", "false").lower() == "true"

CACHE_FILE = "cache.json"

# Only notify for courses containing this keyword
SEM_FILTER = "CSE_II SEM"

session = requests.Session()


def send_discord(msg):
    resp = requests.post(DISCORD_WEBHOOK, json={"content": msg})
    if not resp.ok:
        print(f"[WARN] Discord webhook failed: {resp.status_code} {resp.text}")


def load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def login():
    r = session.get(f"{LMS_URL}/login/index.php")
    if 'name="logintoken" value="' not in r.text:
        raise RuntimeError("Could not find login token — LMS page structure may have changed.")
    token = r.text.split('name="logintoken" value="')[1].split('"')[0]
    payload = {
        "username": USERNAME,
        "password": PASSWORD,
        "logintoken": token,
    }
    resp = session.post(f"{LMS_URL}/login/index.php", data=payload)
    if "loginerrors" in resp.text or "Invalid login" in resp.text:
        raise RuntimeError("Login failed — check LMS_USER and LMS_PASS secrets.")


def get_sesskey():
    page = session.get(f"{LMS_URL}/my/")
    if '"sesskey":"' not in page.text:
        raise RuntimeError("Could not get sesskey — login may have failed silently.")
    return page.text.split('"sesskey":"')[1].split('"')[0]


def fetch_timeline(sesskey):
    url = f"{LMS_URL}/lib/ajax/service.php?sesskey={sesskey}"
    payload = [{
        "index": 0,
        "methodname": "core_calendar_get_action_events_by_timesort",
        "args": {
            "limitnum": 10
        }
    }]
    r = session.post(url, json=payload)
    r.raise_for_status()
    return r.json()


def clean_html(html):
    clean = re.sub(r'<.*?>', '', html)
    clean = re.sub(r'\n{3,}', '\n\n', clean)
    return clean.strip()


def is_sem2_course(course_name):
    return SEM_FILTER.lower() in course_name.lower()


def format_discord_msg(course, name, message, attachments, tag=""):
    header = f"📢  **LMS UPDATE — CSE II SEM**  {tag}".strip()
    return (
        f"{header}"
        "\n\n\n"
        f"📚  **Course:**\n{course}"
        "\n\n\n"
        f"📌  **Title:**\n{name}"
        "\n\n\n"
        f"💬  **Message:**\n{message}"
        "\n\n\n"
        f"📎  **Attachments:** {attachments}"
        "\n\n"
        "─────────────────────────"
    )


def main():
    if not USERNAME or not PASSWORD or not DISCORD_WEBHOOK:
        raise RuntimeError(
            "One or more required env vars are missing: LMS_USER, LMS_PASS, WEBHOOK_URL"
        )

    mode = "MANUAL (all SEM II announcements)" if MANUAL_RUN else "SCHEDULED (new announcements only)"
    print(f"[MODE] {mode}")

    login()
    sesskey  = get_sesskey()
    cache    = load_cache()
    events   = fetch_timeline(sesskey)

    sent = 0

    # ── MANUAL RUN: send a header banner first ──
    if MANUAL_RUN:
        send_discord(
            "📋  **MANUAL FETCH — ALL CSE II SEM ANNOUNCEMENTS**\n\n\n"
            "Below are all current CSE II SEM announcements from your LMS.\n"
            "─────────────────────────"
        )

    for item in events:
        if "data" not in item:
            print(f"[WARN] Skipping item with no 'data' key: {item}")
            continue

        event_list = item["data"].get("events", [])

        for e in event_list:
            name        = e.get("name", "")
            description = e.get("description", "")
            course      = e.get("course", {}).get("fullname", "")
            key         = name + course

            # ── Skip non-SEM_II courses in both modes ──
            if not is_sem2_course(course):
                print(f"[SKIP] Not SEM_II course: {course}")
                continue

            # ── SCHEDULED: skip already-seen events ──
            if not MANUAL_RUN and key in cache:
                continue

            message     = clean_html(description)
            attachments = "✅  Present" if "pluginfile.php" in description else "❌  None"

            # Tag manual messages so you know it's a full dump
            tag = "*(manual)*" if MANUAL_RUN else ""
            discord_msg = format_discord_msg(course, name, message, attachments, tag)

            send_discord(discord_msg)
            sent += 1
            print(f"[SENT] {course} — {name}")

            # ── Only update cache on scheduled runs ──
            # Manual runs don't pollute the cache so scheduled
            # runs still catch genuinely new events afterwards.
            if not MANUAL_RUN:
                cache.append(key)

    # Save cache only on scheduled runs
    if not MANUAL_RUN:
        save_cache(cache)

    label = "announcement(s)" if sent != 1 else "announcement"
    print(f"\nDone. {sent} SEM II {label} sent to Discord.")


if __name__ == "__main__":
    main()    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def login():
    r = session.get(f"{LMS_URL}/login/index.php")
    if 'name="logintoken" value="' not in r.text:
        raise RuntimeError("Could not find login token — LMS page structure may have changed.")
    token = r.text.split('name="logintoken" value="')[1].split('"')[0]
    payload = {
        "username": USERNAME,
        "password": PASSWORD,
        "logintoken": token,
    }
    resp = session.post(f"{LMS_URL}/login/index.php", data=payload)
    if "loginerrors" in resp.text or "Invalid login" in resp.text:
        raise RuntimeError("Login failed — check LMS_USER and LMS_PASS secrets.")


def get_sesskey():
    page = session.get(f"{LMS_URL}/my/")
    if '"sesskey":"' not in page.text:
        raise RuntimeError("Could not get sesskey — login may have failed silently.")
    return page.text.split('"sesskey":"')[1].split('"')[0]


def fetch_timeline(sesskey):
    url = f"{LMS_URL}/lib/ajax/service.php?sesskey={sesskey}"
    payload = [{
        "index": 0,
        "methodname": "core_calendar_get_action_events_by_timesort",
        "args": {
            "limitnum": 10
        }
    }]
    r = session.post(url, json=payload)
    r.raise_for_status()
    return r.json()


def clean_html(html):
    clean = re.sub(r'<.*?>', '', html)
    # Collapse 3+ newlines down to 2
    clean = re.sub(r'\n{3,}', '\n\n', clean)
    return clean.strip()


def is_sem2_course(course_name):
    """Return True only if the course belongs to Semester II."""
    return SEM_FILTER.lower() in course_name.lower()


def format_discord_msg(course, name, message, attachments):
    """
    Build the Discord message with double blank lines between
    each field for clean readability.
    """
    return (
        "📢  **LMS UPDATE — SEM II**"
        "\n\n\n"
        f"📚  **Course:**\n{course}"
        "\n\n\n"
        f"📌  **Title:**\n{name}"
        "\n\n\n"
        f"💬  **Message:**\n{message}"
        "\n\n\n"
        f"📎  **Attachments:** {attachments}"
        "\n\n"
        "─────────────────────────"
    )


def main():
    if not USERNAME or not PASSWORD or not DISCORD_WEBHOOK:
        raise RuntimeError(
            "One or more required env vars are missing: LMS_USER, LMS_PASS, WEBHOOK_URL"
        )

    login()
    sesskey = get_sesskey()
    cache = load_cache()
    events = fetch_timeline(sesskey)

    new_events = 0

    for item in events:
        if "data" not in item:
            print(f"[WARN] Skipping item with no 'data' key: {item}")
            continue

        event_list = item["data"].get("events", [])

        for e in event_list:
            name        = e.get("name", "")
            description = e.get("description", "")
            course      = e.get("course", {}).get("fullname", "")
            key         = name + course

            # ── Skip anything that isn't a SEM_II course ──
            if not is_sem2_course(course):
                print(f"[SKIP] Not a SEM II course: {course}")
                continue

            # ── Skip if already notified ──
            if key in cache:
                continue

            message     = clean_html(description)
            attachments = "✅  Present" if "pluginfile.php" in description else "❌  None"

            discord_msg = format_discord_msg(course, name, message, attachments)
            send_discord(discord_msg)
            cache.append(key)
            new_events += 1
            print(f"[SENT] {course} — {name}")

    save_cache(cache)
    print(f"\nDone. {new_events} new SEM II event(s) sent to Discord.")


if __name__ == "__main__":
    main()
