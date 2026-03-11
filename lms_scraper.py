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
def _extract_row_data(row) -> dict:
    """Pull title/url/date/author/replies from a single table row or div."""
    title_tag = (
        row.select_one("td.topic a")
        or row.select_one("td.subject a")
        or row.select_one("a.w-100")
        or row.select_one("a[href*='discuss.php']")
        or row.select_one("a[href*='forum/discuss']")
    )
    if not title_tag:
        return {}

    title    = title_tag.get_text(strip=True)
    post_url = title_tag.get("href", "")
    if not post_url.startswith("http"):
        post_url = LMS_BASE + post_url

    date_obj = None
    date_str = "Unknown date"
    for sel in ["td.lastpost", "td.created", "td.modified"]:
        td = row.select_one(sel)
        if td:
            date_obj = parse_moodle_date(td.get_text(" ", strip=True))
            if date_obj:
                date_str = date_obj.strftime("%d %b %Y, %I:%M %p")
                break
    if not date_obj:
        for td in row.find_all("td"):
            txt = td.get_text(" ", strip=True)
            d   = parse_moodle_date(txt)
            if d:
                date_obj = d
                date_str = d.strftime("%d %b %Y, %I:%M %p")
                break

    author_tag = (
        row.select_one("td.author a")
        or row.select_one(".author a")
        or row.select_one("td.userpicture + td a")
    )
    author  = author_tag.get_text(strip=True) if author_tag else "Unknown"
    rep_td  = row.select_one("td.replies")
    replies = rep_td.get_text(strip=True) if rep_td else "—"

    return {
        "title":    title,
        "url":      post_url,
        "date":     date_obj,
        "date_str": date_str,
        "author":   author,
        "replies":  replies,
    }


def fetch_post_summary(session: requests.Session, post_url: str) -> tuple[str, str, str]:
    """
    Fetch the discuss.php page and extract:
      - author name
      - posted date string
      - first ~300 chars of post body (clean text, no HTML)
    Returns (author, date_str, summary)
    """
    try:
        r    = session.get(post_url, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        # ── Author ────────────────────────────────────────────────
        author = "Unknown"
        for sel in [
            ".author a", ".username a", ".fullname",
            "[data-region='post'] .username",
            ".forumpost .author a",
            "address.author a",
        ]:
            tag = soup.select_one(sel)
            if tag:
                author = tag.get_text(strip=True)
                break

        # ── Date ─────────────────────────────────────────────────
        date_str = "Unknown date"
        for sel in [
            ".author time", "time[datetime]",
            ".forumpost .date", ".posted", ".lastpost time",
            "[data-region='post'] .date",
        ]:
            tag = soup.select_one(sel)
            if tag:
                raw = tag.get("datetime", "") or tag.get_text(strip=True)
                d   = parse_moodle_date(raw)
                if d:
                    date_str = d.strftime("%d %b %Y, %I:%M %p")
                    break
                elif raw:
                    date_str = raw[:30]
                    break

        # ── Post body ────────────────────────────────────────────
        summary = ""
        for sel in [
            ".posting",                          # classic Moodle
            "[data-region='post-content-container']",
            ".post-content-container",
            ".forumpost .content .no-overflow",
            ".forumpost .posting",
            "div.message",
        ]:
            tag = soup.select_one(sel)
            if tag:
                # Strip inner images/links but keep text
                for t in tag.find_all(["img", "script", "style"]):
                    t.decompose()
                text = tag.get_text(" ", strip=True)
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) > 20:
                    summary = text[:320]
                    if len(text) > 320:
                        summary += "…"
                    break

        return author, date_str, summary

    except Exception as ex:
        print(f"      ⚠️  Could not fetch post body: {ex}")
        return "Unknown", "Unknown date", ""


def fetch_announcements(session: requests.Session, forum_id: str, lookback_hours: int) -> list:
    url    = f"{LMS_BASE}/mod/forum/view.php?id={forum_id}"
    cutoff = datetime.now() - timedelta(hours=lookback_hours)
    r      = session.get(url, timeout=20)
    soup   = BeautifulSoup(r.text, "html.parser")

    print(f"    📄 Page title: {soup.title.string.strip() if soup.title else 'N/A'}")

    # ── Strategy A: known Moodle row selectors ────────────────────
    rows = (
        soup.select("tr.discussion")
        or soup.select("table.forumheaderlist tr")[1:]
        or soup.select(".discussion-list .discussion")
        or []
    )
    print(f"    🔎 Strategy A rows found: {len(rows)}")

    # ── Strategy B: find ALL discuss.php links ────────────────────
    if not rows:
        all_links = soup.find_all("a", href=re.compile(r"forum/discuss\.php|mod/forum/discuss"))
        print(f"    🔎 Strategy B links found: {len(all_links)}")
        results = []
        seen    = set()
        for a in all_links:
            post_url = a.get("href", "")
            if not post_url.startswith("http"):
                post_url = LMS_BASE + post_url
            if post_url in seen:
                continue
            seen.add(post_url)

            title = a.get_text(strip=True)
            if not title:
                continue

            if RUN_MODE == "manual" or True:   # always collect, filter after summary fetch
                results.append({
                    "title":    title,
                    "url":      post_url,
                    "date":     None,
                    "date_str": "Unknown date",
                    "author":   "Unknown",
                    "replies":  "—",
                    "summary":  "",
                })

        # Fetch summaries (cap at 9 to avoid slow runs)
        for post in results[:9]:
            author, date_str, summary = fetch_post_summary(session, post["url"])
            post["author"]   = author
            post["date_str"] = date_str
            post["summary"]  = summary
            # Re-parse date for auto-mode filtering
            d = parse_moodle_date(date_str)
            post["date"] = d or datetime.min

        # Apply cutoff for auto mode
        if RUN_MODE == "auto":
            results = [p for p in results if p["date"] >= cutoff]

        results.sort(key=lambda x: x["date"], reverse=True)
        return results

    # ── Process Strategy A rows ───────────────────────────────────
    results = []
    for row in rows:
        try:
            data = _extract_row_data(row)
            if not data:
                continue

            date_obj = data["date"]
            if RUN_MODE == "manual":
                include = True
            else:
                include = date_obj is not None and date_obj >= cutoff

            if include:
                data["summary"] = ""
                results.append(data)

        except Exception as ex:
            print(f"    ⚠️  Skipped row: {ex}")

    # Fetch post summaries for Strategy A results too
    for post in results[:9]:
        _, _, summary = fetch_post_summary(session, post["url"])
        post["summary"] = summary

    results.sort(key=lambda x: (x["date"] or datetime.min), reverse=True)
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
        if t == 0:
            return None
        if p / t >= target / 100:
            return 0
        needed = 0
        while (p + needed) / (t + needed) < target / 100:
            needed += 1
        return needed
    except Exception:
        return None


def _classes_can_skip(present: str, total: str, target: float = 75.0) -> int | None:
    """How many classes can be skipped while staying at or above `target`%."""
    try:
        p = int(re.sub(r"\D", "", present))
        t = int(re.sub(r"\D", "", total))
        if t == 0 or p / t < target / 100:
            return 0
        can_skip = 0
        while t + can_skip + 1 > 0 and p / (t + can_skip + 1) >= target / 100:
            can_skip += 1
        return can_skip
    except Exception:
        return None


def fetch_attendance(session: requests.Session) -> list | None:
    """
    Scrapes the SIU consolidated attendance report.
    Known table structure (from live page):
      Col 0: Course Name
      Col 1: Total Sessions
      Col 2: Marked Sessions   (same as Total in practice — sessions where attendance was taken)
      Col 3: Attended Sessions (classes the student actually attended)
      Col 4: Percentage
    Footer rows contain summary text ("Total Conducted Session", "Total Percentage") — skipped.
    """
    try:
        r    = session.get(ATTENDANCE_URL, timeout=25)
        soup = BeautifulSoup(r.text, "html.parser")

        if soup.find(id="loginerrormessage") or soup.find(class_="loginerrormessage"):
            print("  ⚠️  Attendance page redirected to login.")
            return None

        records     = []
        total_conducted = None
        total_attended  = None
        total_pct       = None

        table = soup.find("table")
        if table:
            raw_headers = [th.get_text(strip=True) for th in table.find_all("th")]
            print(f"  📋 Headers detected: {raw_headers}")
            headers_lower = [h.lower() for h in raw_headers]

            # ── Map columns from headers ──────────────────────────────
            def col_idx(*keys):
                for k in keys:
                    for i, h in enumerate(headers_lower):
                        if k in h:
                            return i
                return -1

            # Primary detection from known header keywords
            idx_subj     = col_idx("course", "subject", "name", "paper", "module")
            idx_total    = col_idx("total session", "total conducted", "total classes", "total")
            idx_marked   = col_idx("marked")
            idx_attended = col_idx("attended session", "attended")
            idx_pct      = col_idx("percentage", "percent", "attendance %", "%")

            # Fallback: if can't detect from headers, use known fixed positions
            # (based on confirmed live page structure)
            if idx_subj < 0:     idx_subj     = 0
            if idx_total < 0:    idx_total    = 1
            if idx_attended < 0: idx_attended = 3
            if idx_pct < 0:      idx_pct      = 4

            print(f"  📋 Using cols → name:{idx_subj} total:{idx_total} attended:{idx_attended} pct:{idx_pct}")

            for tr in table.find_all("tr")[1:]:  # skip header row
                # Get each <td> text individually — critical to NOT use tr.get_text()
                tds   = tr.find_all("td")
                if not tds:
                    continue
                cells = [td.get_text(strip=True) for td in tds]

                # Skip rows with fewer cells than expected (colspan summary rows)
                if len(cells) < 3:
                    continue

                # ── Grab subject name cleanly from its own <td> ───────
                subj = cells[idx_subj] if idx_subj < len(cells) else ""

                # Skip footer/summary rows
                if not subj or re.search(r"total|grand|summary|conducted|session", subj, re.I):
                    # But capture summary data for the overall footer
                    row_text = tr.get_text(" ", strip=True)
                    m_total  = re.search(r"conducted[^\d]*(\d+)", row_text, re.I)
                    m_att    = re.search(r"attended[^\d]*(\d+)", row_text, re.I)
                    m_pct2   = re.search(r"(\d+\.?\d*)\s*%", row_text)
                    if m_total:  total_conducted = int(m_total.group(1))
                    if m_att:    total_attended  = int(m_att.group(1))
                    if m_pct2:   total_pct       = float(m_pct2.group(1))
                    continue

                # Skip pure-number subject names (malformed rows)
                if re.match(r"^\d+$", subj):
                    continue

                total    = cells[idx_total]    if idx_total    < len(cells) else "—"
                attended = cells[idx_attended] if idx_attended < len(cells) else "—"
                pct_raw  = cells[idx_pct]      if idx_pct      < len(cells) else "—"

                # Clean percentage
                pct = _pct_float(pct_raw)
                # Fallback: compute from attended/total if pct missing
                if pct is None:
                    t = _pct_float(total)
                    a = _pct_float(attended)
                    if t and a and t > 0:
                        pct = round(a / t * 100, 2)

                records.append({
                    "subject":    subj.strip(),
                    "attended":   attended,        # sessions actually attended
                    "total":      total,           # total sessions conducted
                    "percentage": f"{pct:.2f}%" if pct is not None else pct_raw,
                    "pct_float":  pct,
                    "status":     _status(pct),
                })

        if not records:
            print("  ⚠️  No table records found.")
            return None

        print(f"  📋 {len(records)} subject records parsed.")
        return {
            "records":          records,
            "total_conducted":  total_conducted,
            "total_attended":   total_attended,
            "total_pct":        total_pct,
        }

    except Exception as ex:
        import traceback
        print(f"  ❌ Attendance scrape error: {ex}")
        traceback.print_exc()
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
        fields = []
        meta_parts = []
        if post["author"] not in ("Unknown", "", "—"):
            meta_parts.append(f"👤 {post['author']}")
        if post["date_str"] not in ("Unknown date", ""):
            meta_parts.append(f"📅 {post['date_str']}")
        if post["replies"] not in ("—", "", "0"):
            meta_parts.append(f"💬 {post['replies']} repl{'y' if post['replies'] == '1' else 'ies'}")

        # Description = summary (post body preview) if available, else meta
        description = ""
        if post.get("summary"):
            description = post["summary"]
        if meta_parts:
            description = "  •  ".join(meta_parts) + ("\n\n" + description if description else "")

        embed = {
            "title":     f"📢 {post['title']}",
            "url":       post["url"],
            "color":     subject["color"],
            "footer":    {"text": f"{subject['emoji']} {subject['name']} • LMS Notifier"},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        if description:
            embed["description"] = description

        embeds.append(embed)

    # Send in chunks of 10 (Discord limit)
    for i in range(0, len(embeds), 10):
        _post(webhook, {"embeds": embeds[i:i+10]})

    print(f"  ✅ {subject['code']}: sent {len(posts)} post(s).")


def send_attendance_to_discord(data: dict | None) -> None:
    if not ATTENDANCE_WEBHOOK:
        print("⚠️  WEBHOOK_ATTENDANCE not set — skipping.")
        return

    # ── Fetch failed ─────────────────────────────────────────────
    if data is None:
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

    records         = data["records"]
    total_conducted = data.get("total_conducted")
    total_attended  = data.get("total_attended")
    total_pct       = data.get("total_pct")

    # ── Sort: lowest % first (most urgent up top) ────────────────
    records.sort(key=lambda r: r["pct_float"] if r["pct_float"] is not None else 999)

    # ── Build per-subject fields ──────────────────────────────────
    fields = []
    for rec in records[:25]:   # Discord max 25 fields
        pct  = rec["pct_float"]
        att  = rec["attended"]
        tot  = rec["total"]

        lines = [f"{rec['status']}  **{rec['percentage']}**  ({att} / {tot} classes)"]

        # How many classes needed to hit 75%?
        if pct is not None and pct < 75:
            needed = _classes_needed(att, tot, target=75.0)
            if needed is not None:
                lines.append(f"⚠️ Need **{needed}** more class(es) to reach 75%")

        # How many can be safely skipped while staying ≥ 75%?
        if pct is not None and pct >= 75:
            can_skip = _classes_can_skip(att, tot, target=75.0)
            if can_skip and can_skip > 0:
                lines.append(f"✅ Can afford to skip **{can_skip}** class(es)")

        fields.append({
            "name":   rec["subject"][:50],
            "value":  "\n".join(lines),
            "inline": False,
        })

    # ── Summary header ────────────────────────────────────────────
    pct_vals   = [r["pct_float"] for r in records if r["pct_float"] is not None]
    avg_pct    = sum(pct_vals) / len(pct_vals) if pct_vals else 0
    low_count  = sum(1 for p in pct_vals if p < 75)
    border     = sum(1 for p in pct_vals if 75 <= p < 85)
    safe_count = sum(1 for p in pct_vals if p >= 85)
    top_color  = 0xE74C3C if low_count else (0xF39C12 if border else 0x2ECC71)

    # Overall totals from footer (if scraped), else compute from records
    if total_conducted and total_attended:
        overall_line = f"📚 Total: **{total_attended} / {total_conducted}** sessions"
        overall_pct  = total_pct or (round(total_attended / total_conducted * 100, 1))
        overall_line += f"  →  **{overall_pct:.1f}%**"
    else:
        overall_line = f"📊 Avg across subjects: **{avg_pct:.1f}%**"

    summary = (
        f"{overall_line}\n"
        f"🔴 Low (<75%): **{low_count}**  •  "
        f"🟡 Borderline (75–84%): **{border}**  •  "
        f"🟢 Safe (≥85%): **{safe_count}**"
    )

    embed = {
        "title":       "📋 Attendance Report — SIU Hyderabad",
        "description": summary,
        "color":       top_color,
        "fields":      fields,
        "footer":      {"text": f"Attendance Bot • SIU Hyderabad • {datetime.now().strftime('%d %b %Y, %I:%M %p')}"},
        "timestamp":   datetime.utcnow().isoformat() + "Z",
    }

    _post(ATTENDANCE_WEBHOOK, {"embeds": [embed]})
    print(f"✅ Attendance sent — {len(records)} subjects.")


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

    # ── Announcements (Session 1) ─────────────────────────────────
    session = login_to_lms()
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

    # ── Attendance (fresh Session 2 — avoids expiry after 10+ fetches) ──
    run_attendance = (RUN_MODE == "auto") or (
        os.environ.get("FETCH_ATTENDANCE", "false").lower() == "true"
    )

    if run_attendance:
        print("\n📋 Scraping attendance (fresh login)...")
        try:
            att_session = login_to_lms()   # brand-new session — guaranteed fresh
            att_data    = fetch_attendance(att_session)
            count       = len(att_data["records"]) if att_data else 0
            print(f"  🔍 {count} subject records found.")
            send_attendance_to_discord(att_data)
        except Exception as ex:
            import traceback
            print(f"  ❌ Attendance failed: {ex}")
            traceback.print_exc()
            send_attendance_to_discord(None)

    print("\n" + "=" * 60)
    print(f"✅ Finished — {total_new} announcement(s) dispatched.")
    print("=" * 60)


if __name__ == "__main__":
    main()
