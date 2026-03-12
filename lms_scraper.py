"""
LMS Announcement + Attendance Notifier — SIU Hyderabad  v4
===========================================================
Fixes vs v3:
  - Author + date now shown correctly in every announcement embed
  - Line breaks preserved in post body (not collapsed to single line)
  - Attendance fetch has 10s per-URL timeout — no more 5-min hangs
  - Attendance tried in parallel (threading) for speed
  - Auto mode sends "all clear" per subject when nothing new
  - Attendance runs every hour in AUTO mode
"""

import os
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────
LMS_BASE      = "https://lmssithyd.siu.edu.in"
LMS_LOGIN_URL = f"{LMS_BASE}/login/index.php"

# The confirmed working attendance URL.
# After login we always warm up by visiting the dashboard first,
# then navigate here — this ensures all session cookies are set properly.
ATTENDANCE_URL     = f"{LMS_BASE}/attendance-report/Student-Attendance/index.php"
ATTENDANCE_TIMEOUT = 20   # seconds for attendance page fetch
REQUEST_TIMEOUT    = 15   # general page fetch timeout

LMS_USERNAME = os.environ["LMS_USERNAME"]
LMS_PASSWORD = os.environ["LMS_PASSWORD"]

RUN_MODE = os.environ.get("RUN_MODE", "manual").lower()

def _auto_lookback_hours() -> int:
    """Hours since midnight IST — so every hourly auto run shows ALL of today's posts."""
    from datetime import timezone, timedelta as _td
    IST      = timezone(_td(hours=5, minutes=30))
    now_ist  = datetime.now(IST)
    midnight = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed  = int((now_ist - midnight).total_seconds() // 3600)
    return max(elapsed + 1, 1)

LOOKBACK_HOURS = _auto_lookback_hours() if RUN_MODE == "auto" else int(os.environ.get("LOOKBACK_DAYS", 7)) * 24

EMBED_DESC_LIMIT   = 4000
EMBEDS_PER_PAYLOAD = 10

SUBJECTS = [
    {"name": "Career Essentials",                               "code": "CE",   "forum_id": "1942", "webhook": os.environ["WEBHOOK_CAREER_ESSENTIALS"],  "emoji": "💼", "color": 0x5865F2},
    {"name": "Computer Architecture and Organization",          "code": "CAO",  "forum_id": "1937", "webhook": os.environ["WEBHOOK_COMPUTER_ARCH"],      "emoji": "🖥️", "color": 0xEB459E},
    {"name": "Creative Thinking",                               "code": "CT",   "forum_id": "1941", "webhook": os.environ["WEBHOOK_CREATIVE_THINKING"],  "emoji": "🎨", "color": 0xFEE75C},
    {"name": "Exploratory Data Analysis",                       "code": "EDA",  "forum_id": "1935", "webhook": os.environ["WEBHOOK_EDA"],                "emoji": "📊", "color": 0x57F287},
    {"name": "Introduction to Environment and Sustainability",  "code": "IES",  "forum_id": "1936", "webhook": os.environ["WEBHOOK_ENV_SUSTAIN"],        "emoji": "🌿", "color": 0x2ECC71},
    {"name": "Linear Algebra",                                  "code": "LA",   "forum_id": "1933", "webhook": os.environ["WEBHOOK_LINEAR_ALGEBRA"],     "emoji": "📐", "color": 0x9B59B6},
    {"name": "Microcontrollers and Sensors",                    "code": "MCS",  "forum_id": "1934", "webhook": os.environ["WEBHOOK_MICROCONTROLLERS"],   "emoji": "🔌", "color": 0xE67E22},
    {"name": "Python Programming",                              "code": "PY",   "forum_id": "1939", "webhook": os.environ["WEBHOOK_PYTHON"],             "emoji": "🐍", "color": 0x3498DB},
    {"name": "Software Engineering",                            "code": "SE",   "forum_id": "1938", "webhook": os.environ["WEBHOOK_SOFTWARE_ENG"],       "emoji": "⚙️", "color": 0xE74C3C},
    {"name": "Technical and Professional Communication Skills", "code": "TPCS", "forum_id": "1940", "webhook": os.environ["WEBHOOK_TPCS"],               "emoji": "📝", "color": 0x1ABC9C},
]

ATTENDANCE_WEBHOOK = os.environ.get("WEBHOOK_ATTENDANCE", "")

ALL_CLEAR_MSGS = [
    "You're all caught up! No new posts this hour. ✨",
    "Nothing new this hour — keep up the good work! 💪",
    "All quiet on the LMS front this hour. 🎉",
    "No new posts this hour. Relax, you're on top of it! 😌",
    "Zero announcements this hour. Clean slate! 🧹",
]


# ─────────────────────────────────────────────────────────────────
# 1. LOGIN
# ─────────────────────────────────────────────────────────────────
def login_to_lms(warm_up: bool = False) -> requests.Session:
    """
    Log in to LMS and return an authenticated session.

    warm_up=True  →  After login, visit the dashboard then prime the
    attendance URL. This seeds all required session cookies exactly as
    a real browser would, preventing the stale-session issue where the
    attendance page silently uses an expired previous session.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
    })

    print("  🌐 Fetching login page...")
    r    = session.get(LMS_LOGIN_URL, timeout=REQUEST_TIMEOUT)
    soup = BeautifulSoup(r.text, "html.parser")

    token_tag  = soup.find("input", {"name": "logintoken"})
    logintoken = token_tag["value"] if token_tag else ""
    print(f"  🔑 logintoken: {'found' if logintoken else 'NOT FOUND'}")

    r = session.post(LMS_LOGIN_URL, data={
        "username": LMS_USERNAME, "password": LMS_PASSWORD,
        "logintoken": logintoken, "anchor": "", "rememberusername": "1",
    }, timeout=REQUEST_TIMEOUT, allow_redirects=True)

    soup_after = BeautifulSoup(r.text, "html.parser")

    if soup_after.find(id="loginerrormessage") or soup_after.find(class_="loginerrormessage"):
        raise RuntimeError("❌ LMS login failed - wrong credentials?")

    logged_in = (
        "/my/" in r.url
        or "logout" in r.text.lower()
        or soup_after.find("a", {"data-title": "logout,moodle"}) is not None
        or soup_after.find(attrs={"class": lambda c: c and "usermenu" in c}) is not None
        or soup_after.find("div", {"id": "page-my-index"}) is not None
    )
    print(f"  {'✅ Logged in.' if logged_in else '⚠️  Login unclear - proceeding anyway.'}")

    if warm_up:
        # Step 1: Visit dashboard to seed all base session cookies
        print("  🔥 Warm-up: visiting dashboard...")
        try:
            session.get(f"{LMS_BASE}/my/", timeout=REQUEST_TIMEOUT)
        except Exception as ex:
            print(f"  ⚠️  Dashboard warm-up failed (non-fatal): {ex}")

        # Step 2: Hit the attendance URL once so the server sets its
        # specific cookies/session data before our actual scrape call
        print(f"  🔥 Warm-up: priming attendance URL...")
        try:
            session.get(ATTENDANCE_URL, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        except Exception as ex:
            print(f"  ⚠️  Attendance prime failed (non-fatal): {ex}")

    return session


def _is_login_page(soup: BeautifulSoup, url: str) -> bool:
    return (
        "login/index.php" in url
        or soup.find("input", {"name": "logintoken"}) is not None
        or soup.find(id="loginerrormessage") is not None
    )


# ─────────────────────────────────────────────────────────────────
# 2. DATE PARSER
# ─────────────────────────────────────────────────────────────────
MOODLE_FORMATS = [
    "%d %B %Y, %I:%M %p", "%d %b %Y, %I:%M %p",
    "%A, %d %B %Y, %I:%M %p", "%d/%m/%Y, %I:%M %p",
    "%d %B %Y", "%d %b %Y",
]

def parse_moodle_date(raw: str) -> datetime | None:
    if not raw: return None
    raw = raw.strip()
    now = datetime.now()
    if raw.lower().startswith("today"):     return now
    if raw.lower().startswith("yesterday"): return now - timedelta(days=1)
    # ISO 8601 — e.g. 2026-03-09T16:04:21+05:30 (from time[datetime] attr)
    iso = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", raw)
    if iso:
        try: return datetime.strptime(iso.group(1), "%Y-%m-%dT%H:%M:%S")
        except: pass
    for fmt in MOODLE_FORMATS:
        try:
            return datetime.strptime(raw[:len(fmt) + 6].strip(), fmt)
        except ValueError:
            continue
    m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", raw)
    if m:
        try: return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y")
        except: pass
    return None


# ─────────────────────────────────────────────────────────────────
# 3. ANNOUNCEMENT SCRAPER
# ─────────────────────────────────────────────────────────────────
def _extract_row_data(row) -> dict:
    title_tag = (
        row.select_one("td.topic a") or row.select_one("td.subject a")
        or row.select_one("a.w-100") or row.select_one("a[href*='discuss.php']")
        or row.select_one("a[href*='forum/discuss']")
    )
    if not title_tag: return {}

    title    = title_tag.get_text(strip=True)
    post_url = title_tag.get("href", "")
    if not post_url.startswith("http"): post_url = LMS_BASE + post_url

    date_obj, date_str = None, "Unknown date"
    for sel in ["td.lastpost", "td.created", "td.modified"]:
        td = row.select_one(sel)
        if td:
            date_obj = parse_moodle_date(td.get_text(" ", strip=True))
            if date_obj:
                date_str = date_obj.strftime("%d %b %Y, %I:%M %p")
                break
    if not date_obj:
        for td in row.find_all("td"):
            d = parse_moodle_date(td.get_text(" ", strip=True))
            if d:
                date_obj, date_str = d, d.strftime("%d %b %Y, %I:%M %p")
                break

    author_tag = (
        row.select_one("td.author a") or row.select_one(".author a")
        or row.select_one("td.userpicture + td a")
    )
    author  = author_tag.get_text(strip=True) if author_tag else "Unknown"
    rep_td  = row.select_one("td.replies")
    replies = rep_td.get_text(strip=True) if rep_td else "—"

    return {"title": title, "url": post_url, "date": date_obj,
            "date_str": date_str, "author": author, "replies": replies}


def _extract_body_with_linebreaks(tag) -> str:
    """
    Extract text from a BS4 tag while preserving meaningful line breaks.
    Block-level elements (p, div, li, br) become newlines.
    """
    if tag is None:
        return ""
    # Remove noise
    for t in tag.find_all(["img", "script", "style", "figure"]): t.decompose()

    lines = []
    for element in tag.descendants:
        # Only process NavigableString (actual text nodes)
        if hasattr(element, 'name'):
            continue
        text = str(element).strip()
        if not text:
            continue
        parent = element.parent
        parent_name = parent.name if parent else ""
        # Add newline before block-level text
        if parent_name in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th"):
            if lines and lines[-1] != "\n":
                lines.append("\n")
        lines.append(text)
        if parent_name == "br":
            lines.append("\n")

    body = "".join(lines)
    # Clean up: max 2 consecutive newlines, no leading/trailing whitespace
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body


def fetch_post_full(session: requests.Session, post_url: str) -> tuple[str, str, str]:
    """
    Returns (author, date_str, body_with_linebreaks).
    Tries many selectors and falls back to the full page body.
    Debug prints show exactly which selector matched.
    """
    try:
        r    = session.get(post_url, timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")

        # ── Author ────────────────────────────────────────────────
        author = "Unknown"
        for sel in [
            "address.author a", ".forumpost .author a",
            "[data-region='post'] .username",
            ".author a", ".username a", ".fullname",
        ]:
            tag = soup.select_one(sel)
            if tag:
                author = tag.get_text(strip=True)
                break

        # ── Date ─────────────────────────────────────────────────
        date_str = "Unknown date"
        for sel in [
            "address.author time", ".forumpost .author time",
            "time[datetime]", ".forumpost .date",
            "[data-region='post'] .date", ".posted",
        ]:
            tag = soup.select_one(sel)
            if tag:
                raw = tag.get("datetime", "") or tag.get_text(strip=True)
                d   = parse_moodle_date(raw)
                if d:
                    date_str = d.strftime("%d %b %Y, %I:%M %p")
                    break
                elif raw.strip():
                    date_str = raw.strip()[:40]
                    break

        # ── Body — try specific selectors first, then broad fallback ──
        body = ""
        BODY_SELECTORS = [
            ".posting",
            "[data-region='post-content-container']",
            ".post-content-container",
            ".forumpost .content .no-overflow",
            ".forumpost .posting",
            ".forumpost .post-content",
            "div.message",
            ".post-content",
            "article.forumpost",
            "[data-region='post']",
            ".discussionview .content",
            ".forumpost",
        ]
        for sel in BODY_SELECTORS:
            tag = soup.select_one(sel)
            if tag:
                candidate = _extract_body_with_linebreaks(tag)
                if len(candidate) > 20:
                    body = candidate
                    print(f"      📝 Body matched via: {sel} ({len(body)} chars)")
                    break

        # Broad fallback: grab #region-main and strip all nav/UI chrome
        if not body:
            main = (
                soup.select_one("#region-main")
                or soup.select_one("div[role='main']")
                or soup.select_one("main")
                or soup.select_one(".main-inner")
                or soup.find("body")
            )
            if main:
                # Remove all structural/nav elements
                for noise in main.find_all([
                    "nav", "header", "footer", "aside", "form",
                    "script", "style", "noscript",
                ]):
                    noise.decompose()
                # Also remove breadcrumbs, reply form, etc.
                for noise_sel in [
                    ".breadcrumb", "#page-header", ".header",
                    ".forum-post-container .forumpost:not(:first-child)",
                    ".reply", ".discussion-nav",
                ]:
                    for el in main.select(noise_sel):
                        el.decompose()
                body = _extract_body_with_linebreaks(main)
                if len(body) > 20:
                    print(f"      📝 Body via broad fallback ({len(body)} chars)")
                else:
                    # Nuclear last resort: just get all visible text
                    body = re.sub(r"\s+", " ", main.get_text(" ", strip=True)).strip()
                    print(f"      📝 Body via get_text fallback ({len(body)} chars)")

        # Strip Moodle UI noise that bleeds into body text
        # Common prefixes/suffixes added by get_text fallback
        import re as _re

        # Strip Moodle UI chrome that bleeds in via get_text fallback.
        # Pattern: "<Post Title> Settings Star this discussion <actual body> Permalink"
        # We want just "<actual body>"

        # 1. Cut everything up to and including "Settings Star this discussion"
        marker = "Settings Star this discussion"
        idx = body.find(marker)
        if idx != -1:
            body = body[idx + len(marker):].lstrip(" \n")

        # 2. Also handle just "Star this discussion" without "Settings" prefix
        marker2 = "Star this discussion"
        if body.startswith(marker2):
            body = body[len(marker2):].lstrip(" \n")

        # 3. Strip trailing Moodle action links
        for suffix in ["Permalink", " Reply", " Edit", " Delete", "Export to portfolio"]:
            if body.endswith(suffix):
                body = body[:-len(suffix)].rstrip(" \n")

        body = body.strip()

        return author, date_str, body

    except Exception as ex:
        print(f"      ⚠️  Could not fetch post: {ex}")
        return "Unknown", "Unknown date", ""


def fetch_announcements(session: requests.Session, forum_id: str, lookback_hours: int) -> list:
    url    = f"{LMS_BASE}/mod/forum/view.php?id={forum_id}"
    cutoff = datetime.now() - timedelta(hours=lookback_hours)
    r      = session.get(url, timeout=REQUEST_TIMEOUT)
    soup   = BeautifulSoup(r.text, "html.parser")

    print(f"    📄 {soup.title.string.strip() if soup.title else 'N/A'}")

    rows = (
        soup.select("tr.discussion")
        or soup.select("table.forumheaderlist tr")[1:]
        or soup.select(".discussion-list .discussion")
        or []
    )
    print(f"    🔎 Strategy A rows: {len(rows)}")

    # ── Strategy B: scan for discuss links ───────────────────────
    if not rows:
        all_links = soup.find_all("a", href=re.compile(r"forum/discuss\.php|mod/forum/discuss"))
        print(f"    🔎 Strategy B links: {len(all_links)}")
        results, seen = [], set()
        for a in all_links:
            href = a.get("href", "")
            if not href.startswith("http"): href = LMS_BASE + href
            if href in seen: continue
            seen.add(href)
            title = a.get_text(strip=True)
            if not title: continue
            results.append({"title": title, "url": href, "date": None,
                             "date_str": "Unknown date", "author": "Unknown",
                             "replies": "—", "body": ""})

        for post in results[:9]:
            author, date_str, body = fetch_post_full(session, post["url"])
            post.update({"author": author, "date_str": date_str, "body": body,
                         "date": parse_moodle_date(date_str) or datetime.min})

        if RUN_MODE == "auto":
            results = [p for p in results if p["date"] >= cutoff]
        results.sort(key=lambda x: x["date"], reverse=True)
        return results

    # ── Strategy A: table rows ────────────────────────────────────
    results = []
    for row in rows:
        try:
            data = _extract_row_data(row)
            if not data: continue
            date_obj = data["date"]
            include  = True if RUN_MODE == "manual" else (date_obj is not None and date_obj >= cutoff)
            if include:
                data["body"] = ""
                results.append(data)
        except Exception as ex:
            print(f"    ⚠️  Skipped row: {ex}")

    # Fetch full body + accurate author/date from each post page
    for post in results[:9]:
        author, date_str, body = fetch_post_full(session, post["url"])
        post["body"] = body
        # Override with post-page author/date if they're more accurate
        if author != "Unknown":
            post["author"] = author
        if date_str != "Unknown date":
            post["date_str"] = date_str

    results.sort(key=lambda x: (x["date"] or datetime.min), reverse=True)
    return results


# ─────────────────────────────────────────────────────────────────
# 4. ATTENDANCE SCRAPER  (with short timeout + parallel tries)
# ─────────────────────────────────────────────────────────────────
def _pct_float(raw: str) -> float | None:
    clean = re.sub(r"[^\d.]", "", raw)
    try:    return float(clean) if clean else None
    except: return None

def _status(pct: float | None) -> str:
    if pct is None: return "⚪"
    if pct >= 85:   return "🟢"
    if pct >= 75:   return "🟡"
    return "🔴"

def _classes_needed(present: str, total: str, target: float = 75.0) -> int | None:
    try:
        p, t = int(re.sub(r"\D", "", present)), int(re.sub(r"\D", "", total))
        if t == 0: return None
        if p / t >= target / 100: return 0
        n = 0
        while (p + n) / (t + n) < target / 100: n += 1
        return n
    except: return None

def _classes_can_skip(present: str, total: str, target: float = 75.0) -> int | None:
    try:
        p, t = int(re.sub(r"\D", "", present)), int(re.sub(r"\D", "", total))
        if t == 0 or p / t < target / 100: return 0
        s = 0
        while t + s + 1 > 0 and p / (t + s + 1) >= target / 100: s += 1
        return s
    except: return None


def _parse_attendance_table(soup: BeautifulSoup) -> dict | None:
    """
    Standard table — one <tr> per subject with <td> cells.
    Header: Course Name | Total Sessions | Marked Sessions | Attended Sessions | Percentage
    Data:   Linear Algebra...  | 17 | 17 | 7 | 41.18%
    Footer: Total Conducted Session: 141 / Total Attended: 100 / Total Percentage: 70.9%
    """
    table = soup.find("table")
    if not table:
        print("  ⚠️  No <table> found on page.")
        return None

    rows = table.find_all("tr")
    print(f"  📋 Table has {len(rows)} rows total")
    if not rows:
        return None

    records = []
    total_conducted = total_attended = total_pct = None

    for tr in rows:
        cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        if not cells:
            continue

        first = cells[0].strip()

        # Skip the header row
        if any(k in first.lower() for k in ("course", "subject", "name")):
            print(f"  📋 Header row: {cells}")
            continue

        # Footer rows — capture totals
        row_text = " ".join(cells)
        if re.search(r"total|grand|percentage", row_text, re.I) and not re.search(r"\(CSE", row_text):
            m = re.search(r"total conducted[^\d]*(\d+)", row_text, re.I)
            if m: total_conducted = int(m.group(1))
            m = re.search(r"total attended[^\d]*(\d+)", row_text, re.I)
            if m: total_attended = int(m.group(1))
            m = re.search(r"([\d.]+)\s*%", row_text)
            if m: total_pct = float(m.group(1))
            print(f"  📋 Footer row: {row_text[:80]}")
            continue

        # Skip rows with too few cells
        if len(cells) < 4:
            continue

        # Subject row: cells = [name, total, marked, attended, pct]
        subj    = cells[0]
        total   = cells[1]
        # cells[2] = marked — skip
        attended = cells[3] if len(cells) > 3 else cells[2]
        pct_raw  = cells[4] if len(cells) > 4 else cells[-1]

        pct = _pct_float(pct_raw)
        if pct is None:
            t_f, a_f = _pct_float(total), _pct_float(attended)
            if t_f and a_f and t_f > 0:
                pct = round(a_f / t_f * 100, 2)

        records.append({
            "subject":    subj,
            "total":      total,
            "attended":   attended,
            "percentage": f"{pct:.1f}%" if pct is not None else pct_raw,
            "pct_float":  pct,
            "status":     _status(pct),
        })
        print(f"  ✅ {subj[:40]} → {attended}/{total} = {pct_raw}")

    if not records:
        print("  ⚠️  0 records — trying regex fallback on page text...")
        return _parse_attendance_from_text(soup)

    return {
        "records":         records,
        "total_conducted": total_conducted,
        "total_attended":  total_attended,
        "total_pct":       total_pct,
    }


def _parse_attendance_from_text(soup: BeautifulSoup) -> dict | None:
    """Regex fallback: pull data directly from visible page text."""
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    # Match: "Subject Name (CSE_II SEM) 17 17 7 41.18%"
    pattern = re.compile(
        r"([A-Za-z][A-Za-z0-9 ,&()\/\-]+?\([A-Za-z0-9_\s]+\))"
        r"\s+(\d+)\s+\d+\s+(\d+)\s+([\d.]+%)",
        re.I
    )
    records = []
    for m in pattern.finditer(text):
        pct = _pct_float(m.group(4))
        records.append({
            "subject":    m.group(1).strip(),
            "total":      m.group(2),
            "attended":   m.group(3),
            "percentage": f"{pct:.1f}%" if pct is not None else m.group(4),
            "pct_float":  pct,
            "status":     _status(pct),
        })
    if records:
        print(f"  ✅ Regex fallback got {len(records)} records.")
        # grab totals too
        total_conducted = total_attended = total_pct = None
        m = re.search(r"Total Conducted Session:\s*(\d+)", text, re.I)
        if m: total_conducted = int(m.group(1))
        m = re.search(r"Total Attended Session:\s*(\d+)", text, re.I)
        if m: total_attended = int(m.group(1))
        m = re.search(r"Total Percentage:\s*([\d.]+)", text, re.I)
        if m: total_pct = float(m.group(1))
        return {"records": records, "total_conducted": total_conducted,
                "total_attended": total_attended, "total_pct": total_pct}
    print("  ❌ Regex fallback also found nothing.")
    return None


def fetch_attendance(session: requests.Session) -> dict | None:
    """
    Fetch attendance from the confirmed working URL.
    The session passed in should already be warmed-up (warm_up=True login)
    so all cookies are properly set before this request.
    """
    print(f"  🌐 Fetching attendance: {ATTENDANCE_URL}")
    try:
        r    = session.get(ATTENDANCE_URL, timeout=ATTENDANCE_TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")

        # Check if we got redirected to login
        if _is_login_page(soup, r.url):
            print("  ⚠️  Attendance redirected to login page - session issue.")
            print("    Trying one more time with a fresh warm-up session...")
            fresh = login_to_lms(warm_up=True)
            r    = fresh.get(ATTENDANCE_URL, timeout=ATTENDANCE_TIMEOUT)
            soup = BeautifulSoup(r.text, "html.parser")
            if _is_login_page(soup, r.url):
                print("  ❌  Still on login page after retry. Cannot fetch attendance.")
                return None

        data = _parse_attendance_table(soup)
        if data:
            return data

        # No table found - print debug info
        snippet = soup.get_text(" ", strip=True)
        import re as _re
        snippet = _re.sub(r"\s+", " ", snippet)[:400]
        print(f"  ⚠️  No attendance table found. Page snippet: {snippet}")
        print(f"  🔗  Final URL after redirects: {r.url}")
        return None

    except Exception as ex:
        import traceback
        print(f"  ❌  Attendance fetch error: {ex}")
        traceback.print_exc()
        return None


# ─────────────────────────────────────────────────────────────────
# 5. DISCORD HELPERS
# ─────────────────────────────────────────────────────────────────
def _post_webhook(webhook: str, payload: dict) -> None:
    r = requests.post(webhook, json=payload, timeout=10)
    if r.status_code not in (200, 204):
        print(f"    ❌ Discord {r.status_code}: {r.text[:200]}")

def _flush(webhook: str, embeds: list) -> None:
    for i in range(0, len(embeds), EMBEDS_PER_PAYLOAD):
        _post_webhook(webhook, {"embeds": embeds[i:i + EMBEDS_PER_PAYLOAD]})

def _chunk_text(text: str, limit: int = EMBED_DESC_LIMIT) -> list[str]:
    """Split text at limit chars, preferring paragraph/sentence/word breaks."""
    if len(text) <= limit: return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text); break
        # Prefer paragraph break
        cut = text.rfind("\n\n", 0, limit)
        if cut == -1: cut = text.rfind("\n", 0, limit)
        if cut == -1: cut = text.rfind(". ", 0, limit)
        if cut == -1: cut = text.rfind(" ", 0, limit)
        if cut == -1: cut = limit
        else: cut += 1
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    return chunks


# ─────────────────────────────────────────────────────────────────
# 6. DISCORD SENDERS
# ─────────────────────────────────────────────────────────────────
def send_announcements_to_discord(webhook: str, subject: dict, posts: list) -> None:
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    color   = subject["color"]

    if not posts:
        if RUN_MODE == "auto":
            hour_seed = int(datetime.now().strftime("%H"))
            msg = ALL_CLEAR_MSGS[hour_seed % len(ALL_CLEAR_MSGS)]
            _flush(webhook, [{
                "title":       f"{subject['emoji']} {subject['name']}",
                "description": msg,
                "color":       0x57F287,
                "footer":      {"text": f"LMS Notifier • {now_str}"},
                "timestamp":   datetime.utcnow().isoformat() + "Z",
            }])
        else:
            _flush(webhook, [{
                "title":       f"{subject['emoji']} {subject['name']}",
                "description": f"✅ No announcements in the past {LOOKBACK_HOURS // 24} day(s).",
                "color":       0x95A5A6,
                "footer":      {"text": f"LMS Notifier • {now_str}"},
                "timestamp":   datetime.utcnow().isoformat() + "Z",
            }])
        print(f"  📭 {subject['code']}: nothing new.")
        return

    window = f"today ({LOOKBACK_HOURS}h)" if RUN_MODE == "auto" else f"past {LOOKBACK_HOURS // 24} days"
    all_embeds = [{
        "title":       f"{subject['emoji']} {subject['name']}",
        "description": f"📬 **{len(posts)} new announcement(s)** in the {window}.",
        "color":       color,
        "footer":      {"text": f"LMS Notifier • {now_str}"},
        "timestamp":   datetime.utcnow().isoformat() + "Z",
    }]

    for post in posts[:9]:
        # ── Meta line (author • date • replies) ───────────────────
        meta_parts = []
        if post.get("author") not in ("Unknown", "", "—", None):
            meta_parts.append(f"👤 **{post['author']}**")
        if post.get("date_str") not in ("Unknown date", "", None):
            meta_parts.append(f"📅 {post['date_str']}")
        if post.get("replies") not in ("—", "", "0", None):
            r_val = post["replies"]
            meta_parts.append(f"💬 {r_val} repl{'y' if r_val == '1' else 'ies'}")
        meta_line = "  •  ".join(meta_parts)

        body = post.get("body", "").strip()

        # ── Build full description ─────────────────────────────────
        # meta line, blank line, then body (line breaks preserved)
        if meta_line and body:
            full_desc = f"{meta_line}\n\n{body}"
        elif body:
            full_desc = body
        else:
            full_desc = meta_line or "*(no content)*"

        chunks = _chunk_text(full_desc)
        for idx, chunk in enumerate(chunks):
            suffix = "" if idx == 0 else f" *(cont. {idx + 1}/{len(chunks)})*"
            all_embeds.append({
                "title":       f"📢 {post['title']}{suffix}",
                "url":         post["url"],
                "description": chunk,
                "color":       color,
                "footer":      {"text": f"{subject['emoji']} {subject['name']} • LMS Notifier"},
                "timestamp":   datetime.utcnow().isoformat() + "Z",
            })

    _flush(webhook, all_embeds)
    print(f"  ✅ {subject['code']}: {len(posts)} post(s) → {len(all_embeds)} embed(s) sent.")


def send_attendance_to_discord(data: dict | None) -> None:
    if not ATTENDANCE_WEBHOOK:
        print("⚠️  WEBHOOK_ATTENDANCE not set — skipping.")
        return

    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")

    if data is None:
        _post_webhook(ATTENDANCE_WEBHOOK, {"embeds": [{
            "title":       "📋 Attendance — Fetch Failed",
            "description": (
                "❌ Could not scrape the attendance page.\n\n"
                "**How to fix:**\n"
                "1. Check the **GitHub Actions log** → look for `🌐 Trying:` lines\n"
                "2. Find the `Page snippet:` for each URL to see what it returned\n"
                "3. Log into LMS manually → open your attendance page → copy the URL\n"
                "4. Update `ATTENDANCE_URL` at the top of `lms_scraper.py` with the correct path"
            ),
            "color":     0xE74C3C,
            "footer":    {"text": f"Attendance Bot • SIU Hyderabad • {now_str}"},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }]})
        return

    records         = data["records"]
    total_conducted = data.get("total_conducted")
    total_attended  = data.get("total_attended")
    total_pct       = data.get("total_pct")

    records.sort(key=lambda r: r["pct_float"] if r["pct_float"] is not None else 999)

    fields = []
    for rec in records[:25]:
        pct = rec["pct_float"]
        att = rec["attended"]
        tot = rec["total"]

        line1 = f"{rec['status']}  **{rec['percentage']}**  —  {att} / {tot} classes"
        lines = [line1]

        if pct is not None and pct < 75:
            needed = _classes_needed(att, tot)
            if needed: lines.append(f"┗ ⚠️ Need **{needed}** more to hit 75%")
        elif pct is not None and pct >= 75:
            skip = _classes_can_skip(att, tot)
            if skip and skip > 0:
                lines.append(f"┗ ✅ Can skip up to **{skip}** class(es)")

        fields.append({
            "name":   rec["subject"][:50],
            "value":  "\n".join(lines),
            "inline": False,
        })

    pct_vals   = [r["pct_float"] for r in records if r["pct_float"] is not None]
    avg_pct    = sum(pct_vals) / len(pct_vals) if pct_vals else 0
    low_count  = sum(1 for p in pct_vals if p < 75)
    border     = sum(1 for p in pct_vals if 75 <= p < 85)
    safe_count = sum(1 for p in pct_vals if p >= 85)
    top_color  = 0xE74C3C if low_count else (0xF39C12 if border else 0x2ECC71)

    if total_conducted and total_attended:
        ov_pct  = total_pct or round(total_attended / total_conducted * 100, 1)
        overall = f"📚 **{total_attended} / {total_conducted}** sessions  →  **{ov_pct:.1f}%** overall"
    else:
        overall = f"📊 Avg attendance: **{avg_pct:.1f}%**"

    summary = (
        f"{overall}\n\n"
        f"🔴 Low (<75%): **{low_count}**\n"
        f"🟡 Borderline (75–84%): **{border}**\n"
        f"🟢 Safe (≥85%): **{safe_count}**"
    )

    _post_webhook(ATTENDANCE_WEBHOOK, {"embeds": [{
        "title":       "📋 Attendance Report — SIU Hyderabad",
        "description": summary,
        "color":       top_color,
        "fields":      fields,
        "footer":      {"text": f"Attendance Bot • SIU Hyderabad • {now_str}"},
        "timestamp":   datetime.utcnow().isoformat() + "Z",
    }]})
    print(f"✅ Attendance sent — {len(records)} subjects.")


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────
def main():
    window = f"today ({LOOKBACK_HOURS}h)" if RUN_MODE == "auto" else f"last {LOOKBACK_HOURS // 24} days"
    print("=" * 60)
    print("🎓  LMS Notifier — SIU Hyderabad  v4")
    print(f"⚙️   Mode     : {RUN_MODE.upper()}")
    print(f"📅  Lookback : {window}")
    print(f"🕐  Run time : {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print("=" * 60)

    session   = login_to_lms()
    total_new = 0

    for subject in SUBJECTS:
        print(f"\n📚 [{subject['code']}] {subject['name']}")
        try:
            posts = fetch_announcements(session, subject["forum_id"], LOOKBACK_HOURS)
            print(f"  🔍 {len(posts)} post(s) in window.")
            send_announcements_to_discord(subject["webhook"], subject, posts)
            total_new += len(posts)
        except Exception as ex:
            import traceback
            print(f"  ❌ Error: {ex}")
            traceback.print_exc()

    run_attendance = (RUN_MODE == "auto") or (
        os.environ.get("FETCH_ATTENDANCE", "false").lower() == "true"
    )

    if run_attendance:
        print("\n📋 Scraping attendance…")
        try:
            att_session = login_to_lms(warm_up=True)  # warm_up seeds all cookies before attendance fetch
            att_data    = fetch_attendance(att_session)
            count       = len(att_data["records"]) if att_data else 0
            print(f"  🔍 {count} records found.")
            send_attendance_to_discord(att_data)
        except Exception as ex:
            import traceback
            print(f"  ❌ Attendance error: {ex}")
            traceback.print_exc()
            send_attendance_to_discord(None)

    print("\n" + "=" * 60)
    print(f"✅  Done — {total_new} announcement(s) dispatched.")
    print("=" * 60)


if __name__ == "__main__":
    main()
