"""
LMS Announcement + Attendance Notifier — SIU Hyderabad
=======================================================
Modes:
  - MANUAL  (workflow_dispatch): fetches past 7 days of announcements for all subjects
  - AUTO    (schedule, every hr): fetches only new posts in last 1 hour + scrapes attendance

Fixes (v2):
  - Full announcement body (no 320-char truncation); long posts split across multiple Discord msgs
  - Discord 2000-char embed description limit respected with smart chunking
  - Attendance: tries multiple known URL patterns + dumps debug HTML on failure for diagnosis
  - Re-login retry for attendance if first attempt returns login page
"""

import os
import re
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────
LMS_BASE      = "https://lmssithyd.siu.edu.in"
LMS_LOGIN_URL = f"{LMS_BASE}/login/index.php"

# Try multiple known attendance URL patterns
ATTENDANCE_URLS = [
    f"{LMS_BASE}/attendance-report/Student-Attendance/index.php",
    f"{LMS_BASE}/local/attendance/index.php",
    f"{LMS_BASE}/report/attendance/index.php",
    f"{LMS_BASE}/blocks/attendance/index.php",
    f"{LMS_BASE}/mod/attendance/view.php",
]

LMS_USERNAME = os.environ["LMS_USERNAME"]
LMS_PASSWORD = os.environ["LMS_PASSWORD"]

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

# Discord limits
DISCORD_EMBED_DESC_LIMIT = 4000   # Discord allows up to 4096 chars per embed description
DISCORD_EMBEDS_PER_MSG   = 10     # max embeds per webhook POST


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

    print(f"  🌐 Fetching login page: {LMS_LOGIN_URL}")
    r    = session.get(LMS_LOGIN_URL, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")

    token_tag  = soup.find("input", {"name": "logintoken"})
    logintoken = token_tag["value"] if token_tag else ""
    print(f"  🔑 logintoken: {'found' if logintoken else 'NOT FOUND (will try without)'}")

    post_data = {
        "username":         LMS_USERNAME,
        "password":         LMS_PASSWORD,
        "logintoken":       logintoken,
        "anchor":           "",
        "rememberusername": "1",
    }
    r = session.post(LMS_LOGIN_URL, data=post_data, timeout=20, allow_redirects=True)

    final_url  = r.url
    page_lower = r.text.lower()
    soup_after = BeautifulSoup(r.text, "html.parser")

    print(f"  🔗 Post-login URL: {final_url}")

    has_error = (
        'id="loginerrormessage"' in r.text
        or 'class="loginerrormessage"' in r.text
        or soup_after.find(id="loginerrormessage") is not None
        or soup_after.find(class_="loginerrormessage") is not None
    )
    has_success = (
        'data-loginurl' not in r.text
        and (
            "/my/" in final_url
            or "logout" in page_lower
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
        snippet = r.text[:800].replace("\n", " ")
        print(f"  ⚠️  Login result unclear. Page snippet:\n  {snippet}\n")
        print("  ⚠️  Proceeding anyway — will fail gracefully if session is bad.")
    else:
        print("✅ Logged in to LMS successfully.")

    return session


# ─────────────────────────────────────────────────────────────────
# 2. DATE PARSER
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


def fetch_post_full(session: requests.Session, post_url: str) -> tuple[str, str, str]:
    """
    Fetch the discuss.php page and extract:
      - author name
      - posted date string
      - FULL post body text (no truncation)
    Returns (author, date_str, full_body)
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

        # ── Full post body (no truncation) ────────────────────────
        body = ""
        for sel in [
            ".posting",
            "[data-region='post-content-container']",
            ".post-content-container",
            ".forumpost .content .no-overflow",
            ".forumpost .posting",
            "div.message",
        ]:
            tag = soup.select_one(sel)
            if tag:
                for t in tag.find_all(["img", "script", "style"]):
                    t.decompose()
                text = tag.get_text(" ", strip=True)
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) > 20:
                    body = text   # NO truncation — full body
                    break

        return author, date_str, body

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
            results.append({
                "title":    title,
                "url":      post_url,
                "date":     None,
                "date_str": "Unknown date",
                "author":   "Unknown",
                "replies":  "—",
                "body":     "",
            })

        for post in results[:9]:
            author, date_str, body = fetch_post_full(session, post["url"])
            post["author"]   = author
            post["date_str"] = date_str
            post["body"]     = body
            d = parse_moodle_date(date_str)
            post["date"] = d or datetime.min

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
            include  = True if RUN_MODE == "manual" else (date_obj is not None and date_obj >= cutoff)
            if include:
                data["body"] = ""
                results.append(data)
        except Exception as ex:
            print(f"    ⚠️  Skipped row: {ex}")

    for post in results[:9]:
        _, _, body = fetch_post_full(session, post["url"])
        post["body"] = body

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
    if pct is None:   return "⚪ N/A"
    if pct >= 85:     return "🟢 Safe"
    if pct >= 75:     return "🟡 Borderline"
    return "🔴 Low"


def _classes_needed(present: str, total: str, target: float = 75.0) -> int | None:
    try:
        p = int(re.sub(r"\D", "", present))
        t = int(re.sub(r"\D", "", total))
        if t == 0: return None
        if p / t >= target / 100: return 0
        needed = 0
        while (p + needed) / (t + needed) < target / 100:
            needed += 1
        return needed
    except Exception:
        return None


def _classes_can_skip(present: str, total: str, target: float = 75.0) -> int | None:
    try:
        p = int(re.sub(r"\D", "", present))
        t = int(re.sub(r"\D", "", total))
        if t == 0 or p / t < target / 100: return 0
        can_skip = 0
        while t + can_skip + 1 > 0 and p / (t + can_skip + 1) >= target / 100:
            can_skip += 1
        return can_skip
    except Exception:
        return None


def _is_logged_in_page(soup: BeautifulSoup, url: str) -> bool:
    """Return False if the page looks like a login redirect."""
    if soup.find(id="loginerrormessage") or soup.find(class_="loginerrormessage"):
        return False
    if "login/index.php" in url:
        return False
    if soup.find("input", {"name": "logintoken"}):
        return False
    return True


def _parse_attendance_table(soup: BeautifulSoup) -> dict | None:
    records         = []
    total_conducted = None
    total_attended  = None
    total_pct       = None

    table = soup.find("table")
    if not table:
        # Try div-based layout (some Moodle themes)
        return None

    raw_headers   = [th.get_text(strip=True) for th in table.find_all("th")]
    headers_lower = [h.lower() for h in raw_headers]
    print(f"  📋 Headers detected: {raw_headers}")

    def col_idx(*keys):
        for k in keys:
            for i, h in enumerate(headers_lower):
                if k in h:
                    return i
        return -1

    idx_subj     = col_idx("course", "subject", "name", "paper", "module")
    idx_total    = col_idx("total session", "total conducted", "total classes", "total")
    idx_marked   = col_idx("marked")
    idx_attended = col_idx("attended session", "attended")
    idx_pct      = col_idx("percentage", "percent", "attendance %", "%")

    if idx_subj     < 0: idx_subj     = 0
    if idx_total    < 0: idx_total    = 1
    if idx_attended < 0: idx_attended = 3
    if idx_pct      < 0: idx_pct      = 4

    print(f"  📋 Using cols → name:{idx_subj} total:{idx_total} attended:{idx_attended} pct:{idx_pct}")

    for tr in table.find_all("tr")[1:]:
        tds   = tr.find_all("td")
        if not tds:
            continue
        cells = [td.get_text(strip=True) for td in tds]
        if len(cells) < 3:
            continue

        subj = cells[idx_subj] if idx_subj < len(cells) else ""
        if not subj or re.search(r"total|grand|summary|conducted|session", subj, re.I):
            row_text = tr.get_text(" ", strip=True)
            m_total  = re.search(r"conducted[^\d]*(\d+)", row_text, re.I)
            m_att    = re.search(r"attended[^\d]*(\d+)", row_text, re.I)
            m_pct2   = re.search(r"(\d+\.?\d*)\s*%", row_text)
            if m_total: total_conducted = int(m_total.group(1))
            if m_att:   total_attended  = int(m_att.group(1))
            if m_pct2:  total_pct       = float(m_pct2.group(1))
            continue

        if re.match(r"^\d+$", subj):
            continue

        total    = cells[idx_total]    if idx_total    < len(cells) else "—"
        attended = cells[idx_attended] if idx_attended < len(cells) else "—"
        pct_raw  = cells[idx_pct]      if idx_pct      < len(cells) else "—"

        pct = _pct_float(pct_raw)
        if pct is None:
            t = _pct_float(total)
            a = _pct_float(attended)
            if t and a and t > 0:
                pct = round(a / t * 100, 2)

        records.append({
            "subject":    subj.strip(),
            "attended":   attended,
            "total":      total,
            "percentage": f"{pct:.2f}%" if pct is not None else pct_raw,
            "pct_float":  pct,
            "status":     _status(pct),
        })

    if not records:
        return None

    return {
        "records":         records,
        "total_conducted": total_conducted,
        "total_attended":  total_attended,
        "total_pct":       total_pct,
    }


def fetch_attendance(session: requests.Session) -> dict | None:
    """
    Try multiple known attendance URL patterns.
    On failure, dump a debug snippet so we can diagnose the correct URL.
    Retries with a fresh login if the page appears to be a login redirect.
    """
    for att_url in ATTENDANCE_URLS:
        print(f"  🌐 Trying attendance URL: {att_url}")
        try:
            r    = session.get(att_url, timeout=25)
            soup = BeautifulSoup(r.text, "html.parser")

            # Detect login redirect
            if not _is_logged_in_page(soup, r.url):
                print(f"  🔄 Got login redirect at {att_url} — re-logging in...")
                session = login_to_lms()
                r    = session.get(att_url, timeout=25)
                soup = BeautifulSoup(r.text, "html.parser")
                if not _is_logged_in_page(soup, r.url):
                    print(f"  ❌ Still on login page after re-login. Skipping URL.")
                    continue

            # Try to find attendance table
            result = _parse_attendance_table(soup)
            if result:
                print(f"  ✅ Attendance parsed from {att_url}")
                return result

            # No table found — check if page has any useful content
            page_text = soup.get_text(" ", strip=True)[:500]
            print(f"  ⚠️  No table at {att_url}. Page snippet: {page_text}")

            # Try to find attendance links on this page and follow them
            att_links = soup.find_all("a", href=re.compile(r"attendance", re.I))
            for link in att_links[:3]:
                link_url = link.get("href", "")
                if not link_url.startswith("http"):
                    link_url = LMS_BASE + link_url
                print(f"      🔗 Following attendance link: {link_url}")
                r2    = session.get(link_url, timeout=25)
                soup2 = BeautifulSoup(r2.text, "html.parser")
                result2 = _parse_attendance_table(soup2)
                if result2:
                    print(f"  ✅ Attendance parsed from followed link: {link_url}")
                    return result2

        except Exception as ex:
            import traceback
            print(f"  ❌ Error at {att_url}: {ex}")
            traceback.print_exc()

    print("  ❌ All attendance URLs failed.")
    return None


# ─────────────────────────────────────────────────────────────────
# 5. DISCORD HELPERS
# ─────────────────────────────────────────────────────────────────
def _post(webhook: str, payload: dict) -> None:
    r = requests.post(webhook, json=payload, timeout=10)
    if r.status_code not in (200, 204):
        print(f"    ❌ Discord {r.status_code}: {r.text[:200]}")


def _chunk_text(text: str, limit: int = DISCORD_EMBED_DESC_LIMIT) -> list[str]:
    """Split text into chunks ≤ limit chars, breaking at sentence/word boundaries."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Try to break at last sentence end within limit
        cut = text.rfind(". ", 0, limit)
        if cut == -1:
            cut = text.rfind(" ", 0, limit)
        if cut == -1:
            cut = limit
        else:
            cut += 1  # include the space/period
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    return chunks


def _flush_embeds(webhook: str, embeds: list) -> None:
    """Send embeds in batches of DISCORD_EMBEDS_PER_MSG."""
    for i in range(0, len(embeds), DISCORD_EMBEDS_PER_MSG):
        _post(webhook, {"embeds": embeds[i:i + DISCORD_EMBEDS_PER_MSG]})


# ─────────────────────────────────────────────────────────────────
# 6. DISCORD SENDERS
# ─────────────────────────────────────────────────────────────────
def send_announcements_to_discord(webhook: str, subject: dict, posts: list) -> None:
    window = "past 1 hour" if RUN_MODE == "auto" else f"past {LOOKBACK_HOURS // 24} days"

    if not posts:
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

    # ── Header embed ─────────────────────────────────────────────
    header_embed = {
        "title": f"{subject['emoji']} {subject['name']}",
        "description": (
            f"**{len(posts)} new announcement(s)** in the {window}."
            + ("  *(showing latest 9)*" if len(posts) > 9 else "")
        ),
        "color": subject["color"],
        "footer":    {"text": "LMS Notifier • SIU Hyderabad"},
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    all_embeds = [header_embed]

    for post in posts[:9]:
        # ── Meta line ────────────────────────────────────────────
        meta_parts = []
        if post["author"] not in ("Unknown", "", "—"):
            meta_parts.append(f"👤 {post['author']}")
        if post["date_str"] not in ("Unknown date", ""):
            meta_parts.append(f"📅 {post['date_str']}")
        if post.get("replies") not in ("—", "", "0", None):
            meta_parts.append(f"💬 {post['replies']} repl{'y' if post['replies'] == '1' else 'ies'}")
        meta_line = "  •  ".join(meta_parts)

        body = post.get("body", "").strip()

        # ── Build description: meta + full body ───────────────────
        if body:
            full_desc = (meta_line + "\n\n" + body) if meta_line else body
        else:
            full_desc = meta_line or ""

        # ── Split body into chunks if > embed limit ───────────────
        chunks = _chunk_text(full_desc) if full_desc else [""]

        for idx, chunk in enumerate(chunks):
            part_suffix = f" (part {idx + 1}/{len(chunks)})" if len(chunks) > 1 else ""
            embed = {
                "title":     f"📢 {post['title']}{part_suffix}" if idx == 0 else f"📢 {post['title']} (cont.)",
                "url":       post["url"],
                "color":     subject["color"],
                "footer":    {"text": f"{subject['emoji']} {subject['name']} • LMS Notifier"},
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            if chunk:
                embed["description"] = chunk
            all_embeds.append(embed)

    _flush_embeds(webhook, all_embeds)
    print(f"  ✅ {subject['code']}: sent {len(posts)} post(s) across {len(all_embeds)} embed(s).")


def send_attendance_to_discord(data: dict | None) -> None:
    if not ATTENDANCE_WEBHOOK:
        print("⚠️  WEBHOOK_ATTENDANCE not set — skipping.")
        return

    if data is None:
        _post(ATTENDANCE_WEBHOOK, {"embeds": [{
            "title":       "📋 Attendance — Fetch Failed",
            "description": (
                "Could not scrape the attendance page.\n"
                "The LMS may have changed its URL or layout.\n\n"
                "**Debug:** Check the GitHub Actions log for the URLs that were tried "
                "and the page snippets printed. You may need to update `ATTENDANCE_URLS` "
                "in the script with the correct path."
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

    records.sort(key=lambda r: r["pct_float"] if r["pct_float"] is not None else 999)

    fields = []
    for rec in records[:25]:
        pct = rec["pct_float"]
        att = rec["attended"]
        tot = rec["total"]

        lines = [f"{rec['status']}  **{rec['percentage']}**  ({att} / {tot} classes)"]

        if pct is not None and pct < 75:
            needed = _classes_needed(att, tot, target=75.0)
            if needed is not None:
                lines.append(f"⚠️ Need **{needed}** more class(es) to reach 75%")

        if pct is not None and pct >= 75:
            can_skip = _classes_can_skip(att, tot, target=75.0)
            if can_skip and can_skip > 0:
                lines.append(f"✅ Can afford to skip **{can_skip}** class(es)")

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
        overall_line = f"📚 Total: **{total_attended} / {total_conducted}** sessions"
        overall_pct  = total_pct or round(total_attended / total_conducted * 100, 1)
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

    # ── Announcements ─────────────────────────────────────────────
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
            print(f"  ❌ Error: {ex}")

    # ── Attendance ────────────────────────────────────────────────
    run_attendance = (RUN_MODE == "auto") or (
        os.environ.get("FETCH_ATTENDANCE", "false").lower() == "true"
    )

    if run_attendance:
        print("\n📋 Scraping attendance (fresh login)...")
        try:
            att_session = login_to_lms()
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
