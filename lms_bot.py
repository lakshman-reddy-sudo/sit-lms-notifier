import re
import time
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

# Exact course names from your LMS — only announcements from these will be sent
TARGET_COURSES = {
    "Career Essentials - I (CSE_II SEM)",
    "Computer Architecture and Organization (CSE_II SEM)",
    "Creative Thinking (CSE_II SEM)",
    "Exploratory Data Analysis (CSE_II SEM)",
    "Introduction to Environment and Sustainability (CSE_II SEM)",
    "Linear Algebra (CSE_II SEM)",
    "Microcontrollers and Sensors (CSE_II SEM)",
    "Python Programming (CSE_II SEM)",
    "Software Engineering (CSE_II SEM)",
    "Technical and Professional Communication Skills (CSE_II SEM)",
}

DISCORD_LIMIT   = 2000
TRUNCATION_NOTE = "\n\n... *(message truncated — check LMS for full content)*"

session = requests.Session()


# ─── Discord ──────────────────────────────────────────────────────────────────

def send_discord(msg):
    resp = requests.post(DISCORD_WEBHOOK, json={"content": msg})
    if not resp.ok:
        print(f"[WARN] Discord webhook failed: {resp.status_code} {resp.text}")


# ─── Cache ────────────────────────────────────────────────────────────────────

def load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)


# ─── Auth ─────────────────────────────────────────────────────────────────────

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
    print("[OK] Logged in.")


def get_sesskey():
    page = session.get(f"{LMS_URL}/my/")
    if '"sesskey":"' not in page.text:
        raise RuntimeError("Could not get sesskey — login may have failed silently.")
    sesskey = page.text.split('"sesskey":"')[1].split('"')[0]
    print("[OK] Got sesskey.")
    return sesskey


# ─── Timeline ─────────────────────────────────────────────────────────────────

def fetch_timeline(sesskey):
    """
    On MANUAL runs  → fetch events from the past 7 days up to 30 days ahead.
                      This surfaces recently-posted announcements that already
                      passed or have no strict deadline.
    On SCHEDULED runs → fetch only upcoming events (no timesortfrom), which
                        is the default Moodle behaviour.
    """
    url  = f"{LMS_URL}/lib/ajax/service.php?sesskey={sesskey}"
    now  = int(time.time())

    args = {"limitnum": 50}

    if MANUAL_RUN:
        args["timesortfrom"] = now - (7 * 24 * 60 * 60)   # 7 days ago
        args["timesortto"]   = now + (30 * 24 * 60 * 60)  # 30 days ahead
        print("[INFO] Fetching events from the past 7 days + next 30 days.")
    else:
        # Scheduled: only look ahead so we catch new deadlines as they appear
        args["timesortfrom"] = now - (60 * 60)             # 1 hr back (safety buffer)
        args["timesortto"]   = now + (30 * 24 * 60 * 60)  # 30 days ahead
        print("[INFO] Fetching upcoming events.")

    payload = [{
        "index": 0,
        "methodname": "core_calendar_get_action_events_by_timesort",
        "args": args
    }]

    r = session.post(url, json=payload)
    r.raise_for_status()
    return r.json()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def is_target_course(course_name):
    return course_name in TARGET_COURSES


def clean_html(html):
    clean = re.sub(r'<.*?>', '', html or '')
    clean = re.sub(r'\n{3,}', '\n\n', clean)
    return clean.strip()


def format_discord_msg(course, title, message, attachments, tag=""):
    header = f"📢  **LMS ANNOUNCEMENT — CSE II SEM**  {tag}".strip()

    # Measure frame size without body so we know how much space is left
    frame_shell = (
        f"{header}\n\n\n"
        f"📚  **Course:**\n{course}\n\n\n"
        f"📌  **Title:**\n{title}\n\n\n"
        f"💬  **Message:**\n"          # body goes here
        f"\n\n\n"
        f"📎  **Attachments:** {attachments}\n\n"
        "─────────────────────────"
    )

    available = DISCORD_LIMIT - len(frame_shell) - len(TRUNCATION_NOTE)
    if len(message) > available:
        message = message[:available] + TRUNCATION_NOTE

    return (
        f"{header}\n\n\n"
        f"📚  **Course:**\n{course}\n\n\n"
        f"📌  **Title:**\n{title}\n\n\n"
        f"💬  **Message:**\n{message}\n\n\n"
        f"📎  **Attachments:** {attachments}\n\n"
        "─────────────────────────"
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not USERNAME or not PASSWORD or not DISCORD_WEBHOOK:
        raise RuntimeError(
            "One or more required env vars are missing: LMS_USER, LMS_PASS, WEBHOOK_URL"
        )

    mode = "MANUAL (past 7 days + next 30 days)" if MANUAL_RUN else "SCHEDULED (new events only)"
    print(f"[MODE] {mode}")

    login()
    sesskey = get_sesskey()
    cache   = load_cache()
    events  = fetch_timeline(sesskey)

    sent = 0

    if MANUAL_RUN:
        send_discord(
            "📋  **MANUAL FETCH — CSE II SEM ANNOUNCEMENTS (LAST 7 DAYS)**\n\n\n"
            "Below are all CSE II SEM announcements from the past week.\n"
            "─────────────────────────"
        )

    for item in events:
        if "data" not in item:
            print("[WARN] Skipping item with no 'data' key.")
            continue

        event_list = item["data"].get("events", [])

        for e in event_list:
            name        = e.get("name", "")
            description = e.get("description", "")
            course      = e.get("course", {}).get("fullname", "")
            key         = name + course

            # Skip courses not in the whitelist
            if not is_target_course(course):
                print(f"[SKIP] {course}")
                continue

            # Scheduled mode: skip already-notified events
            if not MANUAL_RUN and key in cache:
                continue

            message     = clean_html(description)
            attachments = "✅  Present" if "pluginfile.php" in description else "❌  None"
            tag         = "*(manual)*" if MANUAL_RUN else ""

            discord_msg = format_discord_msg(course, name, message, attachments, tag)
            send_discord(discord_msg)
            sent += 1
            print(f"[SENT] {course} — {name}")

            # Only update cache on scheduled runs
            if not MANUAL_RUN:
                cache.append(key)

    # Save cache only on scheduled runs
    if not MANUAL_RUN:
        save_cache(cache)

    print(f"\nDone. {sent} announcement(s) sent to Discord.")


if __name__ == "__main__":
    main()
