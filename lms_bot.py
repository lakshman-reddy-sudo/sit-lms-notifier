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

# Only notify for courses whose name contains this string (case-insensitive)
SEM_FILTER = "(CSE_II SEM)"

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


def get_sesskey_and_userid():
    page = session.get(f"{LMS_URL}/my/")
    text = page.text
    if '"sesskey":"' not in text:
        raise RuntimeError("Could not get sesskey — login may have failed silently.")
    sesskey = text.split('"sesskey":"')[1].split('"')[0]
    # userid is in the page as "userid":XXXXX
    if '"userid":' not in text:
        raise RuntimeError("Could not get userid from page.")
    userid = text.split('"userid":')[1].split(',')[0].strip()
    print(f"[OK] Got sesskey and userid: {userid}")
    return sesskey, userid


# ─── Moodle Web Service helper ────────────────────────────────────────────────

def ws_call(sesskey, methodname, args):
    """Call a single Moodle web service method via the AJAX endpoint."""
    url = f"{LMS_URL}/lib/ajax/service.php?sesskey={sesskey}"
    payload = [{
        "index": 0,
        "methodname": methodname,
        "args": args
    }]
    r = session.post(url, json=payload)
    r.raise_for_status()
    result = r.json()
    if result and "data" in result[0]:
        return result[0]["data"]
    if result and "error" in result[0]:
        print(f"[WARN] WS error for {methodname}: {result[0]['error']}")
    return None


# ─── Moodle data fetchers ─────────────────────────────────────────────────────

def get_enrolled_courses(sesskey, userid):
    data = ws_call(sesskey, "core_enrol_get_users_courses", {"userid": int(userid)})
    if not data:
        return []
    return data  # list of {id, fullname, ...}


def get_forums_for_courses(sesskey, course_ids):
    data = ws_call(sesskey, "mod_forum_get_forums_by_courses", {"courseids": course_ids})
    if not data:
        return []
    return data  # list of {id, course, name, type, ...}


def get_discussions(sesskey, forum_id):
    data = ws_call(sesskey, "mod_forum_get_forum_discussions", {
        "forumid": forum_id,
        "sortby": "timemodified",
        "sortdirection": "DESC",
        "page": 0,
        "perpage": 10
    })
    if not data:
        return []
    return data.get("discussions", [])


# ─── Helpers ──────────────────────────────────────────────────────────────────

def is_target_course(course_name):
    return SEM_FILTER.lower() in course_name.lower()


def clean_html(html):
    clean = re.sub(r'<.*?>', '', html or '')
    clean = re.sub(r'\n{3,}', '\n\n', clean)
    return clean.strip()


def format_discord_msg(course, title, message, author, tag=""):
    header = f"📢  **LMS ANNOUNCEMENT — CSE II SEM**  {tag}".strip()
    return (
        f"{header}"
        "\n\n\n"
        f"📚  **Course:**\n{course}"
        "\n\n\n"
        f"📌  **Title:**\n{title}"
        "\n\n\n"
        f"✍️  **Posted by:** {author}"
        "\n\n\n"
        f"💬  **Message:**\n{message}"
        "\n\n"
        "─────────────────────────"
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not USERNAME or not PASSWORD or not DISCORD_WEBHOOK:
        raise RuntimeError(
            "One or more required env vars are missing: LMS_USER, LMS_PASS, WEBHOOK_URL"
        )

    mode = "MANUAL (all announcements)" if MANUAL_RUN else "SCHEDULED (new only)"
    print(f"[MODE] {mode}")

    login()
    sesskey, userid = get_sesskey_and_userid()
    cache = load_cache()

    # Step 1: get all enrolled courses, filter to CSE_II SEM only
    all_courses = get_enrolled_courses(sesskey, userid)
    target_courses = [c for c in all_courses if is_target_course(c.get("fullname", ""))]

    print(f"[INFO] Found {len(target_courses)} CSE_II SEM course(s) out of {len(all_courses)} total.")
    for c in target_courses:
        print(f"       • {c['fullname']}")

    if not target_courses:
        print("[WARN] No CSE_II SEM courses found — check SEM_FILTER value.")
        return

    # Step 2: get announcement forums for those courses
    course_ids = [c["id"] for c in target_courses]
    course_map = {c["id"]: c["fullname"] for c in target_courses}

    all_forums = get_forums_for_courses(sesskey, course_ids)
    # Moodle marks the announcements forum with type="news"
    announce_forums = [f for f in all_forums if f.get("type") == "news"]

    print(f"[INFO] Found {len(announce_forums)} announcement forum(s).")

    sent = 0

    if MANUAL_RUN:
        send_discord(
            "📋  **MANUAL FETCH — ALL CSE II SEM ANNOUNCEMENTS**\n\n\n"
            "Below are all current CSE II SEM announcements from your LMS.\n"
            "─────────────────────────"
        )

    # Step 3: fetch discussions (posts) from each forum
    for forum in announce_forums:
        forum_id    = forum["id"]
        course_id   = forum.get("course")
        course_name = course_map.get(course_id, "Unknown Course")

        discussions = get_discussions(sesskey, forum_id)

        for d in discussions:
            title   = d.get("name", "")
            message = clean_html(d.get("message", ""))
            author  = d.get("userfullname", "Unknown")
            key     = f"{course_id}_{d.get('id')}"

            # Scheduled: skip already-seen posts
            if not MANUAL_RUN and key in cache:
                continue

            tag         = "*(manual)*" if MANUAL_RUN else ""
            discord_msg = format_discord_msg(course_name, title, message, author, tag)

            send_discord(discord_msg)
            sent += 1
            print(f"[SENT] {course_name} — {title}")

            if not MANUAL_RUN:
                cache.append(key)

    if not MANUAL_RUN:
        save_cache(cache)

    print(f"\nDone. {sent} announcement(s) sent to Discord.")


if __name__ == "__main__":
    main()
