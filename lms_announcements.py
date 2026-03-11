import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import json
import re

# ─────────────────────────────────────────────
# CONFIG — all values come from environment vars (GitHub Secrets)
# ─────────────────────────────────────────────
LMS_BASE = "https://lmssithyd.siu.edu.in"
LMS_LOGIN_URL = f"{LMS_BASE}/login/index.php"
LMS_USERNAME = os.environ["LMS_USERNAME"]
LMS_PASSWORD = os.environ["LMS_PASSWORD"]

SUBJECTS = [
    {
        "name": "Career Essentials",
        "forum_id": "1942",
        "webhook": os.environ["WEBHOOK_CAREER_ESSENTIALS"],
        "emoji": "💼",
        "color": 0x5865F2,
    },
    {
        "name": "Computer Architecture and Organization",
        "forum_id": "1937",
        "webhook": os.environ["WEBHOOK_COMPUTER_ARCH"],
        "emoji": "🖥️",
        "color": 0xEB459E,
    },
    {
        "name": "Creative Thinking",
        "forum_id": "1941",
        "webhook": os.environ["WEBHOOK_CREATIVE_THINKING"],
        "emoji": "🎨",
        "color": 0xFEE75C,
    },
    {
        "name": "Exploratory Data Analysis",
        "forum_id": "1935",
        "webhook": os.environ["WEBHOOK_EDA"],
        "emoji": "📊",
        "color": 0x57F287,
    },
    {
        "name": "Introduction to Environment and Sustainability",
        "forum_id": "1936",
        "webhook": os.environ["WEBHOOK_ENV_SUSTAIN"],
        "emoji": "🌿",
        "color": 0x2ECC71,
    },
    {
        "name": "Linear Algebra",
        "forum_id": "1933",
        "webhook": os.environ["WEBHOOK_LINEAR_ALGEBRA"],
        "emoji": "📐",
        "color": 0x9B59B6,
    },
    {
        "name": "Microcontrollers and Sensors",
        "forum_id": "1934",
        "webhook": os.environ["WEBHOOK_MICROCONTROLLERS"],
        "emoji": "🔌",
        "color": 0xE67E22,
    },
    {
        "name": "Python Programming",
        "forum_id": "1939",
        "webhook": os.environ["WEBHOOK_PYTHON"],
        "emoji": "🐍",
        "color": 0x3498DB,
    },
    {
        "name": "Software Engineering",
        "forum_id": "1938",
        "webhook": os.environ["WEBHOOK_SOFTWARE_ENG"],
        "emoji": "⚙️",
        "color": 0xE74C3C,
    },
    {
        "name": "Technical and Professional Communication Skills",
        "forum_id": "1940",
        "webhook": os.environ["WEBHOOK_TPCS"],
        "emoji": "📝",
        "color": 0x1ABC9C,
    },
]

ATTENDANCE_WEBHOOK = os.environ.get("WEBHOOK_ATTENDANCE", "")

# ─────────────────────────────────────────────
# STEP 1: Login to LMS
# ─────────────────────────────────────────────
def login_to_lms():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    # Get login token
    r = session.get(LMS_LOGIN_URL, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")
    token_input = soup.find("input", {"name": "logintoken"})
    logintoken = token_input["value"] if token_input else ""

    # Login
    payload = {
        "username": LMS_USERNAME,
        "password": LMS_PASSWORD,
        "logintoken": logintoken,
        "anchor": "",
    }
    r = session.post(LMS_LOGIN_URL, data=payload, timeout=15)

    # Verify login
    if "loginerrormessage" in r.text or "Invalid login" in r.text:
        raise Exception("❌ LMS login failed! Check your credentials.")
    
    print("✅ Logged in to LMS successfully.")
    return session


# ─────────────────────────────────────────────
# STEP 2: Fetch announcements for a forum
# ─────────────────────────────────────────────
def fetch_announcements(session, forum_id, days=7):
    url = f"{LMS_BASE}/mod/forum/view.php?id={forum_id}"
    r = session.get(url, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")

    cutoff = datetime.now() - timedelta(days=days)
    announcements = []

    # Moodle forum posts are in discussion rows
    discussions = soup.select("tr.discussion") or soup.select(".forumpost") or []

    # Try alternative selectors if above is empty
    if not discussions:
        discussions = soup.select("table.forumheaderlist tr")[1:]  # skip header row

    for row in discussions:
        try:
            # Get post title/link
            title_tag = row.select_one("td.topic a") or row.select_one("a.w-100")
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            post_url = title_tag["href"]
            if not post_url.startswith("http"):
                post_url = LMS_BASE + post_url

            # Get date
            date_td = row.select_one("td.lastpost") or row.select_one("td.created")
            date_text = date_td.get_text(strip=True) if date_td else ""
            post_date = parse_moodle_date(date_text)

            # Get author
            author_tag = row.select_one("td.author a") or row.select_one(".author")
            author = author_tag.get_text(strip=True) if author_tag else "Unknown"

            # Filter by date
            if post_date and post_date >= cutoff:
                announcements.append({
                    "title": title,
                    "url": post_url,
                    "date": post_date,
                    "author": author,
                    "date_str": post_date.strftime("%d %b %Y, %I:%M %p") if post_date else date_text,
                })
        except Exception as e:
            print(f"  ⚠️ Skipping a row due to error: {e}")
            continue

    # Sort newest first
    announcements.sort(key=lambda x: x["date"] or datetime.min, reverse=True)
    return announcements


def parse_moodle_date(date_str):
    """Parse various Moodle date formats."""
    if not date_str:
        return None
    
    date_str = date_str.strip()
    
    # Handle relative dates
    now = datetime.now()
    if "Today" in date_str:
        return now
    if "Yesterday" in date_str:
        return now - timedelta(days=1)
    
    # Try common Moodle formats
    formats = [
        "%d %B %Y, %I:%M %p",
        "%d %b %Y, %I:%M %p",
        "%A, %d %B %Y, %I:%M %p",
        "%d/%m/%Y, %I:%M %p",
        "%d %B %Y",
        "%d %b %Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str[:len(fmt)+5].strip(), fmt)
        except ValueError:
            continue
    
    # Try to extract a date using regex
    match = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", date_str)
    if match:
        try:
            return datetime.strptime(f"{match.group(1)} {match.group(2)} {match.group(3)}", "%d %B %Y")
        except:
            pass

    return None


# ─────────────────────────────────────────────
# STEP 3: Send to Discord webhook
# ─────────────────────────────────────────────
def send_to_discord(webhook_url, subject, announcements):
    if not announcements:
        # Send "no new announcements" message
        payload = {
            "embeds": [{
                "title": f"{subject['emoji']} {subject['name']}",
                "description": "✅ No new announcements in the past 7 days.",
                "color": 0x95A5A6,
                "footer": {"text": "LMS Notifier • SIU Hyderabad"},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }]
        }
        r = requests.post(webhook_url, json=payload, timeout=10)
        print(f"  📭 {subject['name']}: No announcements sent.")
        return

    # Send each announcement as a rich embed (max 10 embeds per request)
    embeds = []
    for ann in announcements[:10]:  # Discord limit: 10 embeds
        embeds.append({
            "title": f"📢 {ann['title']}",
            "url": ann["url"],
            "color": subject["color"],
            "fields": [
                {"name": "👤 Posted by", "value": ann["author"], "inline": True},
                {"name": "📅 Date", "value": ann["date_str"], "inline": True},
            ],
            "footer": {"text": f"{subject['emoji']} {subject['name']} • LMS Notifier"},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })

    # Header embed
    header = {
        "title": f"{subject['emoji']} {subject['name']}",
        "description": f"**{len(announcements)} announcement(s)** from the past 7 days:",
        "color": subject["color"],
    }

    # Send header + announcement embeds (split if needed)
    all_embeds = [header] + embeds
    
    # Discord allows max 10 embeds per message
    for i in range(0, len(all_embeds), 10):
        chunk = all_embeds[i:i+10]
        payload = {"embeds": chunk}
        r = requests.post(webhook_url, json=payload, timeout=10)
        if r.status_code not in (200, 204):
            print(f"  ❌ Failed to send: {r.status_code} - {r.text}")
        else:
            print(f"  ✅ {subject['name']}: Sent {len(announcements)} announcement(s).")


# ─────────────────────────────────────────────
# STEP 4: Attendance bot (placeholder for future)
# ─────────────────────────────────────────────
def send_attendance_summary(attendance_data=None):
    if not ATTENDANCE_WEBHOOK:
        print("⚠️ No attendance webhook configured, skipping.")
        return

    payload = {
        "embeds": [{
            "title": "📋 Attendance Bot",
            "description": attendance_data or "Attendance tracking is active. Updates will appear here each hour.",
            "color": 0xF39C12,
            "footer": {"text": "Attendance Bot • SIU Hyderabad"},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }]
    }
    r = requests.post(ATTENDANCE_WEBHOOK, json=payload, timeout=10)
    if r.status_code in (200, 204):
        print("✅ Attendance bot notified.")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
def main():
    print("=" * 55)
    print("🎓 LMS Announcement Notifier — SIU Hyderabad")
    print(f"📅 Fetching announcements from past 7 days")
    print("=" * 55)

    # Login
    session = login_to_lms()

    total_sent = 0

    # Process each subject
    for subject in SUBJECTS:
        print(f"\n📚 Processing: {subject['name']} (Forum ID: {subject['forum_id']})")
        try:
            announcements = fetch_announcements(session, subject["forum_id"], days=7)
            print(f"  🔍 Found {len(announcements)} announcement(s) in past 7 days.")
            send_to_discord(subject["webhook"], subject, announcements)
            total_sent += len(announcements)
        except Exception as e:
            print(f"  ❌ Error processing {subject['name']}: {e}")

    print("\n" + "=" * 55)
    print(f"✅ Done! Total announcements sent: {total_sent}")
    print("=" * 55)


if __name__ == "__main__":
    main()
