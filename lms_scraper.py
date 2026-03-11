"""
LMS Announcement + Attendance Notifier — SIU Hyderabad
=======================================================
Modes:
  - MANUAL  (workflow_dispatch): fetches past 7 days of announcements for all subjects
  - AUTO    (schedule, every hr): fetches only new posts in last 1 hour + scrapes attendance
"""

import os
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────
LMS_BASE       = "https://lmssithyd.siu.edu.in"
LMS_LOGIN_URL  = f"{LMS_BASE}/login/index.php"
ATTENDANCE_URL = f"{LMS_BASE}/attendance-report/Student-Attendance/index.php"

LMS_USERNAME = os.environ["LMS_USERNAME"]
LMS_PASSWORD = os.environ["LMS_PASSWORD"]

# "auto" (hourly cron) or "manual" (workflow_dispatch)
RUN_MODE       = os.environ.get("RUN_MODE", "manual").lower()
LOOKBACK_HOURS = 1 if RUN_MODE == "auto" else int(os.environ.get("LOOKBACK_DAYS", 7)) * 24

SUBJECTS = [
    {
        "name": "Career Essentials",             "code": "CE",
        "forum_id": "1942",
        "webhook": os.environ["WEBHOOK_CAREER_ESSENTIALS"],
        "emoji": "💼", "color": 0x5865F2,
    },
    {
        "name": "Computer Architecture and Organization", "code": "CAO",
        "forum_id": "1937",
        "webhook": os.environ["WEBHOOK_COMPUTER_ARCH"],
        "emoji": "🖥️", "color": 0xEB459E,
    },
    {
        "name": "Creative Thinking",             "code": "CT",
        "forum_id": "1941",
        "webhook": os.environ["WEBHOOK_CREATIVE_THINKING"],
        "emoji": "🎨", "color": 0xFEE75C,
    },
    {
        "name": "Exploratory Data Analysis",     "code": "EDA",
        "forum_id": "1935",
        "webhook": os.environ["WEBHOOK_EDA"],
        "emoji": "📊", "color": 0x57F287,
    },
    {
        "name": "Introduction to Environment and Sustainability", "code": "IES",
        "forum_id": "1936",
        "webhook": os.environ["WEBHOOK_ENV_SUSTAIN"],
        "emoji": "🌿", "color": 0x2ECC71,
    },
    {
        "name": "Linear Algebra",                "code": "LA",
        "forum_id": "1933",
        "webhook": os.environ["WEBHOOK_LINEAR_ALGEBRA"],
        "emoji": "📐", "color": 0x9B59B6,
    },
    {
        "name": "Microcontrollers and Sensors",  "code": "MCS",
        "forum_id": "1934",
        "webhook": os.environ["WEBHOOK_MICROCONTROLLERS"],
        "emoji": "🔌", "color": 0xE67E22,
    },
    {
        "name": "Python Programming",            "code": "PY",
        "forum_id": "1939",
        "webhook": os.environ["WEBHOOK_PYTHON"],
        "emoji": "🐍", "color": 0x3498DB,
    },
    {
        "name": "Software Engineering",          "code": "SE",
        "forum_id": "1938",
        "webhook": os.environ["WEBHOOK_SOFTWARE_ENG"],
        "emoji": "⚙️", "color": 0xE74C3C,
    },
    {
        "name": "Technical and Professional Communication Skills", "code": "TPCS",
        "forum_id": "1940",
        "webhook": os.environ["WEBHOOK_TPCS"],
        "emoji": "📝", "color": 0x1ABC9C,
    },
]

ATTENDANCE_WEBHOOK = os.environ.get("WEBHOOK_ATTENDANCE", "")


# ─────────────────────────────────────────────────────────────────
# 1. LOGIN
# ─────────────────────────────────────────────────────────────────
def login_to_lms() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
    })

    # ── Step 1: GET login page → grab logintoken ─────────────────
    print(f"  🌐 Fetching login page: {LMS_LOGIN_URL}")
    r    = session.get(LMS_LOGIN_URL, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")

    token_tag  = soup.find("input", {"name": "logintoken"})
    logintoken = token_tag["value"] if token_tag else ""
    print(f"  🔑 logintoken: {'found' if logintoken else 'NOT FOUND (will try without)'}")

    # ── Step 2: POST credentials ─────────────────────────────────
    post_data = {
        "username":   LMS_USERNAME,
        "password":   LMS_PASSWORD,
        "logintoken": logintoken,
        "anchor":     "",
        "rememberusername": "1",
    }
    r = session.post(LMS_LOGIN_URL, data=post_data, timeout=20, allow_redirects=True)

    # ── Step 3: Verify — check for SUCCESS, not failure ──────────
    # After a good login Moodle redirects to /my/ (dashboard)
    # and the page contains logout link or user menu
    final_url   = r.url
    page_lower  = r.text.lower()
    soup_after  = BeautifulSoup(r.text, "html.parser")

    print(f"  🔗 Post-login URL: {final_url}")

    # Explicit failure markers
    has_error = (
        'id="loginerrormessage"' in r.text
        or 'class="loginerrormessage"' in r.text
        or soup_after.find(id="loginerrormessage") is not None
        or soup_after.find(class_="loginerrormessage") is not None
    )

    # Success markers — logged-in Moodle pages always have these
    has_success = (
        'data-loginurl' not in r.text           # login form gone
        and (
            "/my/" in final_url                 # redirected to dashboard
            or "logout" in page_lower           # logout link present
            or soup_after.find("a", {"data-title": "logout,moodle"}) is not None
            or soup_after.find(attrs={"class": lambda c: c and "usermenu" in c}) is not None
            or soup_after.find("div", {"id": "page-my-index"}) is not None
        )
    )

    if has_error:
        raise RuntimeError(
            "❌ LMS login failed — Moodle returned an error message.\n"
            "   Double-check LMS_USERNAME and LMS_PASSWORD secrets."
        )

    if not has_success:
        # Ambiguous — still on login page? Print snippet for debugging
        snippet = r.text[:800].replace("\n", " ")
        print(f"  ⚠️  Login result unclear. Page snippet:\n  {snippet}\n")
        # Don't hard-fail — attempt to continue; the forum fetches will
        # naturally return empty/redirect if the session is really invalid
        print("  ⚠️  Proceeding anyway — will fail gracefully if session is bad.")
    else:
        print("✅ Logged in to LMS successfully.")

    return session


# ─────────────────────────────────────────────────────────────────
# 2. DATE PARSER  (handles Moodle's many formats)
# ─────────────────────────────────────────────────────────────────
MOODLE_FORMATS = [
    "%d %B %Y, %I:%M %p",
    "%d %b %Y, %I:%M %p",
    "%A, %d %B %Y, %I:%M %p",
    "%d/%m/%Y, %I:%M %p",
    "%d %B %Y",
    "%d %b %Y",
]

def parse_moodle_date(raw: str) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    now = datetime.now()

    if raw.lower().startswith("today"):
        return now
    if raw.lower().startswith("yesterday"):
        return now - timedelta(days=1)

    for fmt in MOODLE_FORMATS:
        try:
            return datetime.strptime(raw[: len(fmt) + 6].strip(), fmt)
        except ValueError:
            continue

    m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", raw)
    if m:
        try:
            return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y")
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────────────────────────
# 3. ANNOUNCEMENT SCRAPER
# ─────────────────────────────────────────────────────────────────
def fetch_announcements(session: requests.Session, forum_id: str, lookback_hours: int) -> list:
    url    = f"{LMS_BASE}/mod/forum/view.php?id={forum_id}"
    cutoff = datetime.now() - timedelta(hours=lookback_hours)
    r      = session.get(url, timeout=20)
    soup   = BeautifulSoup(r.text, "html.parser")

    # Try all known Moodle forum table layouts
    rows = (
        soup.select("tr.discussion")
        or soup.select("table.forumheaderlist tr")[1:]
        or soup.select(".discussion-list .discussion")
        or []
    )

    results = []
    for row in rows:
        try:
            title_tag = (
                row.select_one("td.topic a")
                or row.select_one("a.w-100")
                or row.select_one("td.subject a")
            )
            if not title_tag:
                continue

            title    = title_tag.get_text(strip=True)
            post_url = title_tag.get("href", "")
            if not post_url.startswith("http"):
                post_url = LMS_BASE + post_url

            # Date — try multiple columns
            date_obj = None
            for sel in ["td.lastpost", "td.created", "td.modified"]:
                td = row.select_one(sel)
                if td:
                    date_obj = parse_moodle_date(td.get_text(" ", strip=True))
                    if date_obj:
                        break

            # Author
            author_tag = row.select_one("td.author a") or row.select_one(".author a")
            author     = author_tag.get_text(strip=True) if author_tag else "Unknown"

            # Replies
            rep_td  = row.select_one("td.replies")
            replies = rep_td.get_text(strip=True) if rep_td else "—"

            if date_obj and date_obj >= cutoff:
                results.append({
                    "title":    title,
                    "url":      post_url,
                    "date":     date_obj,
                    "date_str": date_obj.strftime("%d %b %Y, %I:%M %p"),
                    "author":   author,
                    "replies":  replies,
                })

        except Exception as ex:
            print(f"    ⚠️  Skipped row: {ex}")

    results.sort(key=lambda x: x["date"], reverse=True)
    return results


# ─────────────────────────────────────────────────────────────────
# 4. ATTENDANCE SCRAPER
# ─────────────────────────────────────────────────────────────────
def _pct_float(raw: str) -> float | None:
    clean = re.sub(r"[^\d.]", "", raw)
    try:
        return float(clean) if clean else None
    except ValueError:
        return None


def _status(pct: float | None) -> str:
    if pct is None:
        return "⚪ N/A"
    if pct >= 85:
        return "🟢 Safe"
    if pct >= 75:
        return "🟡 Borderline"
    return "🔴 Low"


def _classes_needed(present: str, total: str, target: float = 75.0) -> int | None:
    """How many consecutive classes needed to reach `target`%."""
    try:
        p = int(re.sub(r"\D", "", present))
        t = int(re.sub(r"\D", "", total))
        if p / t >= target / 100:
            return 0
        needed = 0
        while (p + needed) / (t + needed) < target / 100:
            needed += 1
        return needed
    except Exception:
        return None


def fetch_attendance(session: requests.Session) -> list | None:
    try:
        r    = session.get(ATTENDANCE_URL, timeout=25)
        soup = BeautifulSoup(r.text, "html.parser")

        # Redirect-to-login check
        if "loginerrormessage" in r.text or r.url != ATTENDANCE_URL:
            print("  ⚠️  Attendance page session issue.")
            return None

        records = []

        # ── Strategy A: HTML <table> ─────────────────────────────────
        table = soup.find("table")
        if table:
            raw_headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]

            def col_idx(*keys):
                for k in keys:
                    for i, h in enumerate(raw_headers):
                        if k in h:
                            return i
                return -1

            idx_subj    = col_idx("subject", "course", "name")
            idx_present = col_idx("present", "attended")
            idx_absent  = col_idx("absent")
            idx_total   = col_idx("total", "conducted", "classes")
            idx_pct     = col_idx("percent", "%", "attendance")

            for tr in table.find_all("tr")[1:]:
                cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                if not cells or len(cells) < 2:
                    continue

                def cell(idx):
                    return cells[idx] if 0 <= idx < len(cells) else "—"

                subj    = cell(idx_subj)    if idx_subj    >= 0 else cells[0]
                present = cell(idx_present) if idx_present >= 0 else "—"
                absent  = cell(idx_absent)  if idx_absent  >= 0 else "—"
                total   = cell(idx_total)   if idx_total   >= 0 else "—"
                pct_raw = cell(idx_pct)     if idx_pct     >= 0 else "—"

                pct = _pct_float(pct_raw)

                if subj and subj != "—":
                    records.append({
                        "subject":    subj,
                        "present":    present,
                        "absent":     absent,
                        "total":      total,
                        "percentage": f"{pct:.1f}%" if pct is not None else pct_raw,
                        "pct_float":  pct,
                        "status":     _status(pct),
                    })

        # ── Strategy B: card/div layout ──────────────────────────────
        if not records:
            for card in soup.select(".attendance-card, .subject-row, [class*='attend']"):
                text  = card.get_text(" ", strip=True)
                m_pct = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
                name  = (
                    card.select_one(".subject-name, .course-name, h3, h4, strong")
                    or card
                )
                subj = name.get_text(strip=True)[:60]
                pct  = float(m_pct.group(1)) if m_pct else None
                records.append({
                    "subject":    subj,
                    "present":    "—",
                    "absent":     "—",
                    "total":      "—",
                    "percentage": f"{pct:.1f}%" if pct else "—",
                    "pct_float":  pct,
                    "status":     _status(pct),
                })

        return records or None

    except Exception as ex:
        print(f"  ❌ Attendance scrape error: {ex}")
        return None


# ─────────────────────────────────────────────────────────────────
# 5. DISCORD SENDERS
# ─────────────────────────────────────────────────────────────────
def _post(webhook: str, payload: dict) -> None:
    r = requests.post(webhook, json=payload, timeout=10)
    if r.status_code not in (200, 204):
        print(f"    ❌ Discord {r.status_code}: {r.text[:200]}")


def send_announcements_to_discord(webhook: str, subject: dict, posts: list) -> None:
    window = "past 1 hour" if RUN_MODE == "auto" else f"past {LOOKBACK_HOURS // 24} days"

    if not posts:
        # In auto mode stay silent; in manual mode confirm all-clear
        if RUN_MODE == "manual":
            _post(webhook, {"embeds": [{
                "title":       f"{subject['emoji']} {subject['name']}",
                "description": f"✅ No announcements in the {window}.",
                "color":       0x95A5A6,
                "footer":      {"text": "LMS Notifier • SIU Hyderabad"},
                "timestamp":   datetime.utcnow().isoformat() + "Z",
            }]})
        print(f"  📭 {subject['code']}: nothing new.")
        return

    header = {
        "title":       f"{subject['emoji']} {subject['name']}",
        "description": (
            f"**{len(posts)} new announcement(s)** in the {window}."
            + ("  *(showing latest 9)*" if len(posts) > 9 else "")
        ),
        "color": subject["color"],
    }

    embeds = [header]
    for post in posts[:9]:
        embeds.append({
            "title":  f"📢 {post['title']}",
            "url":    post["url"],
            "color":  subject["color"],
            "fields": [
                {"name": "👤 Author",  "value": post["author"],   "inline": True},
                {"name": "📅 Posted",  "value": post["date_str"], "inline": True},
                {"name": "💬 Replies", "value": post["replies"],  "inline": True},
            ],
            "footer":    {"text": f"{subject['emoji']} {subject['name']} • LMS Notifier"},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })

    # Send in chunks of 10 (Discord limit)
    for i in range(0, len(embeds), 10):
        _post(webhook, {"embeds": embeds[i:i+10]})

    print(f"  ✅ {subject['code']}: sent {len(posts)} post(s).")


def send_attendance_to_discord(records: list | None) -> None:
    if not ATTENDANCE_WEBHOOK:
        print("⚠️  WEBHOOK_ATTENDANCE not set — skipping.")
        return

    # ── Fetch failed ─────────────────────────────────────────────
    if records is None:
        _post(ATTENDANCE_WEBHOOK, {"embeds": [{
            "title":       "📋 Attendance — Fetch Failed",
            "description": (
                "Could not scrape the attendance page.\n"
                "The LMS layout may have changed, or the session expired."
            ),
            "color":     0xE74C3C,
            "footer":    {"text": "Attendance Bot • SIU Hyderabad"},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }]})
        return

    # ── Sort: lowest % first (most urgent) ───────────────────────
    records.sort(key=lambda r: r["pct_float"] if r["pct_float"] is not None else 999)

    # ── Build fields ─────────────────────────────────────────────
    fields = []
    for rec in records[:25]:   # Discord max 25 fields
        pct   = rec["pct_float"]
        lines = [f"{rec['status']}  **{rec['percentage']}**"]

        if rec["present"] != "—":
            lines.append(f"Present: **{rec['present']}** / {rec['total']}  |  Absent: {rec['absent']}")

        if pct is not None and pct < 75:
            needed = _classes_needed(rec["present"], rec["total"])
            if needed is not None:
                lines.append(f"Need **{needed}** more class(es) to hit 75%")

        fields.append({
            "name":   rec["subject"][:50],
            "value":  "\n".join(lines),
            "inline": False,
        })

    # ── Summary stats ─────────────────────────────────────────────
    pct_vals   = [r["pct_float"] for r in records if r["pct_float"] is not None]
    avg_pct    = sum(pct_vals) / len(pct_vals) if pct_vals else 0
    low_count  = sum(1 for p in pct_vals if p < 75)
    border     = sum(1 for p in pct_vals if 75 <= p < 85)
    safe_count = sum(1 for p in pct_vals if p >= 85)

    top_color  = 0xE74C3C if low_count else (0xF39C12 if border else 0x2ECC71)

    summary = (
        f"📊 **Overall average:** {avg_pct:.1f}%\n"
        f"🔴 Low (<75%): {low_count}   "
        f"🟡 Borderline (75–84%): {border}   "
        f"🟢 Safe (≥85%): {safe_count}"
    )

    embed = {
        "title":       "📋 Attendance Report",
        "description": summary,
        "color":       top_color,
        "fields":      fields,
        "footer":      {"text": "Attendance Bot • SIU Hyderabad"},
        "timestamp":   datetime.utcnow().isoformat() + "Z",
    }

    _post(ATTENDANCE_WEBHOOK, {"embeds": [embed]})
    print(f"✅ Attendance sent — {len(records)} subjects, avg {avg_pct:.1f}%.")


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────
def main():
    window = (
        "last 1 hour"
        if RUN_MODE == "auto"
        else f"last {LOOKBACK_HOURS // 24} days"
    )

    print("=" * 60)
    print("🎓  LMS Notifier — SIU Hyderabad")
    print(f"⚙️   Mode     : {RUN_MODE.upper()}")
    print(f"📅  Lookback : {window}")
    print(f"🕐  Run time : {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print("=" * 60)

    session = login_to_lms()

    # ── Announcements ────────────────────────────────────────────
    total_new = 0
    for subject in SUBJECTS:
        print(f"\n📚 [{subject['code']}] {subject['name']}")
        try:
            posts = fetch_announcements(session, subject["forum_id"], LOOKBACK_HOURS)
            print(f"  🔍 {len(posts)} post(s) in window.")
            send_announcements_to_discord(subject["webhook"], subject, posts)
            total_new += len(posts)
        except Exception as ex:
            print(f"  ❌ Error: {ex}")

    # ── Attendance ───────────────────────────────────────────────
    # Always in AUTO mode; in MANUAL only if FETCH_ATTENDANCE=true
    run_attendance = (RUN_MODE == "auto") or (
        os.environ.get("FETCH_ATTENDANCE", "false").lower() == "true"
    )

    if run_attendance:
        print("\n📋 Scraping attendance...")
        records = fetch_attendance(session)
        print(f"  🔍 {len(records) if records else 0} records found.")
        send_attendance_to_discord(records)

    print("\n" + "=" * 60)
    print(f"✅ Finished — {total_new} announcement(s) dispatched.")
    print("=" * 60)


if __name__ == "__main__":
    main()
