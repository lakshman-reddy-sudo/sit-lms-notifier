import re
import time
import requests
import json
import os
from datetime import datetime, timezone

LMS_URL = "https://lmssithyd.siu.edu.in"

USERNAME        = os.getenv("LMS_USER")
PASSWORD        = os.getenv("LMS_PASS")
DISCORD_WEBHOOK = os.getenv("WEBHOOK_URL")

# Set to "true" by the workflow when triggered manually
MANUAL_RUN = os.getenv("MANUAL_RUN", "false").lower() == "true"

CACHE_FILE = "cache.json"

# Exact course names — we'll auto-discover their forum IDs by scraping
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

# On manual run: show posts from this many days back
MANUAL_LOOKBACK_DAYS = 30

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
        raise RuntimeError("Could not find login token.")
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


# ─── Course discovery ─────────────────────────────────────────────────────────

def get_course_links():
    """
    Scrape the dashboard to find course page URLs.
    Returns dict: {course_fullname: course_url}
    """
    page = session.get(f"{LMS_URL}/my/")
    text = page.text

    # Moodle renders course links as /course/view.php?id=NNN
    course_links = {}
    for match in re.finditer(r'href="([^"]+/course/view\.php\?id=(\d+))"[^>]*>(.*?)</a>', text, re.DOTALL):
        url        = match.group(1)
        name_raw   = re.sub(r'<.*?>', '', match.group(3)).strip()
        if name_raw in TARGET_COURSES:
            course_links[name_raw] = url if url.startswith("http") else LMS_URL + url

    print(f"[INFO] Found {len(course_links)} target course(s) on dashboard.")
    return course_links


# ─── Forum discovery ──────────────────────────────────────────────────────────

def get_announcement_forum_url(course_url, course_name):
    """
    Scrape a course page to find the Announcements forum link.
    Moodle always has one forum with the word 'Announcement' in it.
    Returns forum URL or None.
    """
    page = session.get(course_url)
    text = page.text

    # Look for forum links — Moodle uses /mod/forum/view.php?id=NNN
    for match in re.finditer(r'href="([^"]+/mod/forum/view\.php\?id=\d+)"[^>]*>(.*?)</a>', text, re.DOTALL):
        url        = match.group(1)
        label      = re.sub(r'<.*?>', '', match.group(2)).strip().lower()
        if "announce" in label or "news" in label:
            full_url = url if url.startswith("http") else LMS_URL + url
            print(f"[INFO] Found announcement forum for '{course_name}': {full_url}")
            return full_url

    print(f"[WARN] No announcement forum found for '{course_name}' — skipping.")
    return None


# ─── Forum scraping ───────────────────────────────────────────────────────────

def scrape_forum_posts(forum_url):
    """
    Scrape a Moodle forum page and return list of dicts:
      {title, author, timestamp, post_url, message_preview}
    """
    page  = session.get(forum_url)
    text  = page.text
    posts = []

    # Each discussion row in Moodle contains a link to the discussion
    # Pattern: /mod/forum/discuss.php?d=NNN
    for match in re.finditer(
        r'href="([^"]+/mod/forum/discuss\.php\?d=(\d+))"[^>]*>(.*?)</a>',
        text, re.DOTALL
    ):
        url      = match.group(1)
        disc_id  = match.group(2)
        title    = re.sub(r'<.*?>', '', match.group(3)).strip()
        if not title:
            continue
        full_url = url if url.startswith("http") else LMS_URL + url
        posts.append({
            "disc_id":  disc_id,
            "title":    title,
            "post_url": full_url,
        })

    # Deduplicate by disc_id (same link may appear multiple times in the DOM)
    seen    = set()
    unique  = []
    for p in posts:
        if p["disc_id"] not in seen:
            seen.add(p["disc_id"])
            unique.append(p)

    return unique


def scrape_discussion_body(discuss_url):
    """
    Fetch the first post body + author from a discussion page.
    Returns (author, message_text)
    """
    page = session.get(discuss_url)
    text = page.text

    author  = "Unknown"
    message = ""

    # Author is usually in a link inside the post header
    author_match = re.search(
        r'class="[^"]*author[^"]*"[^>]*>.*?href="[^"]*"[^>]*>(.*?)</a>',
        text, re.DOTALL
    )
    if author_match:
        author = re.sub(r'<.*?>', '', author_match.group(1)).strip()

    # Message body is inside div class="posting" or class="post-content-container"
    body_match = re.search(
        r'class="[^"]*(posting|post-content-container)[^"]*"[^>]*>(.*?)</div>',
        text, re.DOTALL
    )
    if body_match:
        message = clean_html(body_match.group(2))

    return author, message


# ─── Helpers ──────────────────────────────────────────────────────────────────

def clean_html(html):
    clean = re.sub(r'<.*?>', '', html or '')
    clean = re.sub(r'&amp;',  '&',  clean)
    clean = re.sub(r'&lt;',   '<',  clean)
    clean = re.sub(r'&gt;',   '>',  clean)
    clean = re.sub(r'&nbsp;', ' ',  clean)
    clean = re.sub(r'\n{3,}', '\n\n', clean)
    return clean.strip()


def format_discord_msg(course, title, author, message, post_url, tag=""):
    header = f"📢  **LMS ANNOUNCEMENT — CSE II SEM**  {tag}".strip()

    frame_shell = (
        f"{header}\n\n\n"
        f"📚  **Course:**\n{course}\n\n\n"
        f"📌  **Title:**\n{title}\n\n\n"
        f"✍️  **Posted by:** {author}\n\n\n"
        f"💬  **Message:**\n"
        f"\n\n\n"
        f"🔗  **Link:** {post_url}\n\n"
        "─────────────────────────"
    )

    available = DISCORD_LIMIT - len(frame_shell) - len(TRUNCATION_NOTE)
    if len(message) > available:
        message = message[:available] + TRUNCATION_NOTE

    return (
        f"{header}\n\n\n"
        f"📚  **Course:**\n{course}\n\n\n"
        f"📌  **Title:**\n{title}\n\n\n"
        f"✍️  **Posted by:** {author}\n\n\n"
        f"💬  **Message:**\n{message}\n\n\n"
        f"🔗  **Link:** {post_url}\n\n"
        "─────────────────────────"
    )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    if not USERNAME or not PASSWORD or not DISCORD_WEBHOOK:
        raise RuntimeError(
            "One or more required env vars are missing: LMS_USER, LMS_PASS, WEBHOOK_URL"
        )

    mode = f"MANUAL (last {MANUAL_LOOKBACK_DAYS} days, ignoring cache)" if MANUAL_RUN else "SCHEDULED (new posts only)"
    print(f"[MODE] {mode}")

    login()
    get_sesskey()   # keeps session alive / validates login
    cache = load_cache()

    # Step 1 — find course pages on the dashboard
    course_links = get_course_links()

    if not course_links:
        print("[WARN] No target courses found on dashboard. Check course names match exactly.")
        return

    sent = 0

    if MANUAL_RUN:
        send_discord(
            f"📋  **MANUAL FETCH — CSE II SEM ANNOUNCEMENTS**\n\n\n"
            f"Fetching all announcements from the last {MANUAL_LOOKBACK_DAYS} days.\n"
            "─────────────────────────"
        )

    # Step 2 — for each course, find its announcement forum and scrape posts
    for course_name, course_url in course_links.items():
        forum_url = get_announcement_forum_url(course_url, course_name)
        if not forum_url:
            continue

        posts = scrape_forum_posts(forum_url)
        print(f"[INFO] '{course_name}' — {len(posts)} post(s) found.")

        for post in posts:
            key = f"{course_name}_{post['disc_id']}"

            # Scheduled: skip already-notified posts
            if not MANUAL_RUN and key in cache:
                continue

            # Fetch the full post body
            author, message = scrape_discussion_body(post["post_url"])
            tag             = "*(manual)*" if MANUAL_RUN else ""

            discord_msg = format_discord_msg(
                course_name,
                post["title"],
                author,
                message,
                post["post_url"],
                tag
            )

            send_discord(discord_msg)
            sent += 1
            print(f"[SENT] {course_name} — {post['title']}")

            # Only update cache on scheduled runs
            if not MANUAL_RUN:
                cache.append(key)

            # Small delay to avoid hammering the LMS
            time.sleep(0.5)

    if not MANUAL_RUN:
        save_cache(cache)

    print(f"\nDone. {sent} announcement(s) sent to Discord.")


if __name__ == "__main__":
    main()
