"""
LMS Announcement + Attendance Notifier — SIU Hyderabad
=======================================================
Modes:
  auto    (hourly cron)      : new posts in last 1 hr + attendance every hour
  today   (manual dispatch)  : all posts since midnight IST + attendance
  manual  (manual dispatch)  : posts in last N days, attendance optional
"""

import os
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, timezone

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
LMS_BASE      = "https://lmssithyd.siu.edu.in"
LMS_LOGIN_URL = f"{LMS_BASE}/login/index.php"

# All known / likely attendance page paths — tried in order
ATTENDANCE_URLS = [
    f"{LMS_BASE}/attendance-report/Student-Attendance/index.php",
    f"{LMS_BASE}/attendance-report/index.php",
    f"{LMS_BASE}/local/attendance/index.php",
    f"{LMS_BASE}/local/attendancereport/index.php",
    f"{LMS_BASE}/report/attendance/index.php",
    f"{LMS_BASE}/blocks/attendance/index.php",
    f"{LMS_BASE}/mod/attendance/view.php",
    f"{LMS_BASE}/my/",   # dashboard — will hunt for attendance links here
]

LMS_USERNAME = os.environ["LMS_USERNAME"]
LMS_PASSWORD = os.environ["LMS_PASSWORD"]

RUN_MODE = os.environ.get("RUN_MODE", "manual").lower()

# Compute lookback window
if RUN_MODE == "auto":
    LOOKBACK_HOURS = 1
elif RUN_MODE == "today":
    # Hours since midnight IST (UTC+5:30)
    IST = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(IST)
    midnight_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = now_ist - midnight_ist
    LOOKBACK_HOURS = max(1, int(elapsed.total_seconds() / 3600) + 1)
else:
    LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_DAYS", 7)) * 24

# Discord limits
EMBED_DESC_LIMIT   = 4000
EMBEDS_PER_PAYLOAD = 10

SUBJECTS = [
    {"name": "Career Essentials",                              "code": "CE",   "forum_id": "1942", "webhook": os.environ["WEBHOOK_CAREER_ESSENTIALS"],  "emoji": "💼", "color": 0x5865F2},
    {"name": "Computer Architecture and Organization",         "code": "CAO",  "forum_id": "1937", "webhook": os.environ["WEBHOOK_COMPUTER_ARCH"],      "emoji": "🖥️", "color": 0xEB459E},
    {"name": "Creative Thinking",                              "code": "CT",   "forum_id": "1941", "webhook": os.environ["WEBHOOK_CREATIVE_THINKING"],   "emoji": "🎨", "color": 0xFEE75C},
    {"name": "Exploratory Data Analysis",                      "code": "EDA",  "forum_id": "1935", "webhook": os.environ["WEBHOOK_EDA"],                 "emoji": "📊", "color": 0x57F287},
    {"name": "Introduction to Environment and Sustainability", "code": "IES",  "forum_id": "1936", "webhook": os.environ["WEBHOOK_ENV_SUSTAIN"],         "emoji": "🌿", "color": 0x2ECC71},
    {"name": "Linear Algebra",                                 "code": "LA",   "forum_id": "1933", "webhook": os.environ["WEBHOOK_LINEAR_ALGEBRA"],      "emoji": "📐", "color": 0x9B59B6},
    {"name": "Microcontrollers and Sensors",                   "code": "MCS",  "forum_id": "1934", "webhook": os.environ["WEBHOOK_MICROCONTROLLERS"],    "emoji": "🔌", "color": 0xE67E22},
    {"name": "Python Programming",                             "code": "PY",   "forum_id": "1939", "webhook": os.environ["WEBHOOK_PYTHON"],              "emoji": "🐍", "color": 0x3498DB},
    {"name": "Software Engineering",                           "code": "SE",   "forum_id": "1938", "webhook": os.environ["WEBHOOK_SOFTWARE_ENG"],        "emoji": "⚙️", "color": 0xE74C3C},
    {"name": "Technical and Professional Communication Skills","code": "TPCS", "forum_id": "1940", "webhook": os.environ["WEBHOOK_TPCS"],                "emoji": "📝", "color": 0x1ABC9C},
]

ATTENDANCE_WEBHOOK = os.environ.get("WEBHOOK_ATTENDANCE", "")


# ─────────────────────────────────────────────────────────────────────────────
# 1. LOGIN
# ─────────────────────────────────────────────────────────────────────────────
def login_to_lms(warm_up: bool = False) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
    })

    print("  🌐 Fetching login page…")
    r    = session.get(LMS_LOGIN_URL, timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")

    token_tag  = soup.find("input", {"name": "logintoken"})
    logintoken = token_tag["value"] if token_tag else ""
    print(f"  🔑 logintoken: {'found' if logintoken else 'NOT FOUND'}")

    r = session.post(LMS_LOGIN_URL, data={
        "username": LMS_USERNAME, "password": LMS_PASSWORD,
        "logintoken": logintoken, "anchor": "", "rememberusername": "1",
    }, timeout=20, allow_redirects=True)

    final_url  = r.url
    soup_after = BeautifulSoup(r.text, "html.parser")

    if soup_after.find(id="loginerrormessage") or soup_after.find(class_="loginerrormessage"):
        raise RuntimeError("❌ LMS login failed — wrong credentials?")

    logged_in = (
        "/my/" in final_url
        or "logout" in r.text.lower()
        or soup_after.find("a", {"data-title": "logout,moodle"}) is not None
        or soup_after.find(attrs={"class": lambda c: c and "usermenu" in c}) is not None
        or soup_after.find("div", {"id": "page-my-index"}) is not None
    )

    if logged_in:
        print("  ✅ Logged in successfully.")
    else:
        print(f"  ⚠️  Login unclear (URL={final_url}). Proceeding anyway.")

    if warm_up:
        # Visit dashboard + first attendance URL to seed session cookies
        print("  🔥 Warming up session…")
        try:
            session.get(f"{LMS_BASE}/my/", timeout=15)
            session.get(ATTENDANCE_URLS[0], timeout=15)
            print("  ✅ Warm-up done.")
        except Exception as ex:
            print(f"  ⚠️  Warm-up failed (non-fatal): {ex}")

    return session


def _is_login_page(soup: BeautifulSoup, url: str) -> bool:
    return (
        "login/index.php" in url
        or soup.find("input", {"name": "logintoken"}) is not None
        or soup.find(id="loginerrormessage") is not None
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. DATE PARSER
# ─────────────────────────────────────────────────────────────────────────────
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
    for fmt in MOODLE_FORMATS:
        try:
            return datetime.strptime(raw[:len(fmt) + 6].strip(), fmt)
        except ValueError:
            continue
    m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", raw)
    if m:
        try: return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y")
        except Exception: pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 3. ANNOUNCEMENT SCRAPER
# ─────────────────────────────────────────────────────────────────────────────
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


def fetch_post_full(session: requests.Session, post_url: str) -> tuple[str, str, str]:
    """Returns (author, date_str, full_body_text) — no truncation."""
    try:
        r    = session.get(post_url, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")

        author = "Unknown"
        for sel in [".author a", ".username a", ".fullname",
                    "[data-region='post'] .username", ".forumpost .author a", "address.author a"]:
            tag = soup.select_one(sel)
            if tag: author = tag.get_text(strip=True); break

        date_str = "Unknown date"
        for sel in [".author time", "time[datetime]", ".forumpost .date",
                    ".posted", ".lastpost time", "[data-region='post'] .date"]:
            tag = soup.select_one(sel)
            if tag:
                raw = tag.get("datetime", "") or tag.get_text(strip=True)
                d   = parse_moodle_date(raw)
                date_str = d.strftime("%d %b %Y, %I:%M %p") if d else raw[:30]
                break

        body = ""
        for sel in [".posting", "[data-region='post-content-container']",
                    ".post-content-container", ".forumpost .content .no-overflow",
                    ".forumpost .posting", "div.message"]:
            tag = soup.select_one(sel)
            if tag:
                for t in tag.find_all(["img", "script", "style"]): t.decompose()
                # Strip Moodle UI noise
                for noise in tag.find_all(string=re.compile(
                    r"Settings|Star this discussion|Subscribe|Reply|Permalink", re.I
                )):
                    if noise.parent: noise.parent.decompose()
                text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
                if len(text) > 20:
                    body = text  # full body, no truncation
                    break

        return author, date_str, body
    except Exception as ex:
        print(f"      ⚠️  Could not fetch post: {ex}")
        return "Unknown", "Unknown date", ""


def fetch_announcements(session: requests.Session, forum_id: str, lookback_hours: int) -> list:
    url    = f"{LMS_BASE}/mod/forum/view.php?id={forum_id}"
    cutoff = datetime.now() - timedelta(hours=lookback_hours)
    r      = session.get(url, timeout=20)
    soup   = BeautifulSoup(r.text, "html.parser")

    print(f"    📄 {soup.title.string.strip() if soup.title else 'N/A'}")

    rows = (
        soup.select("tr.discussion")
        or soup.select("table.forumheaderlist tr")[1:]
        or soup.select(".discussion-list .discussion")
        or []
    )
    print(f"    🔎 Strategy A rows: {len(rows)}")

    if not rows:
        all_links = soup.find_all("a", href=re.compile(r"forum/discuss\.php|mod/forum/discuss"))
        print(f"    🔎 Strategy B links: {len(all_links)}")
        results, seen = [], set()
        for a in all_links:
            post_url = a.get("href", "")
            if not post_url.startswith("http"): post_url = LMS_BASE + post_url
            if post_url in seen: continue
            seen.add(post_url)
            title = a.get_text(strip=True)
            if not title: continue
            results.append({"title": title, "url": post_url, "date": None,
                             "date_str": "Unknown date", "author": "Unknown",
                             "replies": "—", "body": ""})

        for post in results[:9]:
            author, date_str, body = fetch_post_full(session, post["url"])
            post.update({"author": author, "date_str": date_str, "body": body,
                         "date": parse_moodle_date(date_str) or datetime.min})

        if RUN_MODE in ("auto", "today"):
            results = [p for p in results if p["date"] >= cutoff]
        results.sort(key=lambda x: x["date"], reverse=True)
        return results

    results = []
    for row in rows:
        try:
            data = _extract_row_data(row)
            if not data: continue
            date_obj = data["date"]
            if RUN_MODE in ("auto", "today"):
                include = date_obj is not None and date_obj >= cutoff
            else:
                include = True
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


# ─────────────────────────────────────────────────────────────────────────────
# 4. ATTENDANCE SCRAPER
# ─────────────────────────────────────────────────────────────────────────────
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
    table = soup.find("table")
    if not table: return None

    raw_headers   = [th.get_text(strip=True) for th in table.find_all("th")]
    headers_lower = [h.lower() for h in raw_headers]
    print(f"  📋 Headers: {raw_headers}")

    def col_idx(*keys):
        for k in keys:
            for i, h in enumerate(headers_lower):
                if k in h: return i
        return -1

    idx_subj     = col_idx("course", "subject", "name", "paper", "module")
    idx_total    = col_idx("total session", "total conducted", "total classes", "total")
    idx_attended = col_idx("attended session", "attended")
    idx_pct      = col_idx("percentage", "percent", "attendance %", "%")

    if idx_subj     < 0: idx_subj     = 0
    if idx_total    < 0: idx_total    = 1
    if idx_attended < 0: idx_attended = 3
    if idx_pct      < 0: idx_pct      = 4

    print(f"  📋 Columns → subj:{idx_subj} total:{idx_total} attended:{idx_attended} pct:{idx_pct}")

    records = []
    total_conducted = total_attended = total_pct = None

    for tr in table.find_all("tr")[1:]:
        tds   = tr.find_all("td")
        if not tds: continue
        cells = [td.get_text(strip=True) for td in tds]
        if len(cells) < 3: continue

        subj = cells[idx_subj] if idx_subj < len(cells) else ""
        if not subj or re.search(r"total|grand|summary|conducted|session", subj, re.I):
            row_text = tr.get_text(" ", strip=True)
            m = re.search(r"conducted[^\d]*(\d+)", row_text, re.I)
            if m: total_conducted = int(m.group(1))
            m = re.search(r"attended[^\d]*(\d+)", row_text, re.I)
            if m: total_attended = int(m.group(1))
            m = re.search(r"(\d+\.?\d*)\s*%", row_text)
            if m: total_pct = float(m.group(1))
            continue
        if re.match(r"^\d+$", subj): continue

        total    = cells[idx_total]    if idx_total    < len(cells) else "—"
        attended = cells[idx_attended] if idx_attended < len(cells) else "—"
        pct_raw  = cells[idx_pct]      if idx_pct      < len(cells) else "—"

        pct = _pct_float(pct_raw)
        if pct is None:
            t, a = _pct_float(total), _pct_float(attended)
            if t and a and t > 0: pct = round(a / t * 100, 2)

        records.append({
            "subject":    subj.strip(),
            "attended":   attended,
            "total":      total,
            "percentage": f"{pct:.1f}%" if pct is not None else pct_raw,
            "pct_float":  pct,
            "status":     _status(pct),
        })

    if not records:
        print("  ⚠️  Table found but no records parsed.")
        return None

    return {"records": records, "total_conducted": total_conducted,
            "total_attended": total_attended, "total_pct": total_pct}


def fetch_attendance(session: requests.Session) -> dict | None:
    """
    Try every URL in ATTENDANCE_URLS.
    Re-login once if redirected to login page.
    Follow attendance links found on any page.
    Print debug snippets so the correct URL can be identified from Action logs.
    """
    tried_urls: set = set()

    def _get_and_parse(sess: requests.Session, url: str) -> dict | None:
        if url in tried_urls: return None
        tried_urls.add(url)
        print(f"  🌐 Trying: {url}")
        try:
            r    = sess.get(url, timeout=25)
            soup = BeautifulSoup(r.text, "html.parser")

            if _is_login_page(soup, r.url):
                print("  🔄 Login redirect detected — re-logging in…")
                new_sess = login_to_lms()
                r2   = new_sess.get(url, timeout=25)
                soup = BeautifulSoup(r2.text, "html.parser")
                if _is_login_page(soup, r2.url):
                    print("  ❌ Still on login page after re-login. Skipping.")
                    return None
                return _parse_and_follow(new_sess, soup, url)

            return _parse_and_follow(sess, soup, url)
        except Exception as ex:
            print(f"  ❌ Request error at {url}: {ex}")
            return None

    def _parse_and_follow(sess: requests.Session, soup: BeautifulSoup, source_url: str) -> dict | None:
        result = _parse_attendance_table(soup)
        if result:
            return result

        snippet = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:300]
        print(f"  ⚠️  No table found. Page snippet: {snippet}")

        for a_tag in soup.find_all("a", href=True):
            href = a_tag.get("href", "")
            text = a_tag.get_text(strip=True).lower()
            if re.search(r"attendance", href + text, re.I):
                link_url = href if href.startswith("http") else LMS_BASE + href
                if link_url in tried_urls: continue
                print(f"  🔗 Following attendance link: {link_url}")
                res = _get_and_parse(sess, link_url)
                if res: return res
        return None

    for url in ATTENDANCE_URLS:
        result = _get_and_parse(session, url)
        if result:
            return result

    print("  ❌ All attendance URLs exhausted — could not parse attendance.")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 5. DISCORD HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _post_webhook(webhook: str, payload: dict) -> None:
    r = requests.post(webhook, json=payload, timeout=10)
    if r.status_code not in (200, 204):
        print(f"    ❌ Discord {r.status_code}: {r.text[:200]}")

def _flush(webhook: str, embeds: list) -> None:
    for i in range(0, len(embeds), EMBEDS_PER_PAYLOAD):
        _post_webhook(webhook, {"embeds": embeds[i:i + EMBEDS_PER_PAYLOAD]})

def _chunk_text(text: str, limit: int = EMBED_DESC_LIMIT) -> list[str]:
    if len(text) <= limit: return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text); break
        cut = text.rfind(". ", 0, limit)
        if cut == -1: cut = text.rfind(" ", 0, limit)
        if cut == -1: cut = limit
        else: cut += 1
        chunks.append(text[:cut].strip())
        text = text[cut:].strip()
    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# 6. DISCORD SENDERS
# ─────────────────────────────────────────────────────────────────────────────
ALL_CLEAR_MESSAGES = [
    "You're all caught up! No new posts this hour. ✨",
    "Nothing new this hour — keep up the good work! 💪",
    "All quiet on the LMS front this hour. 🎉",
    "No new posts this hour. Relax, you're on top of it! 😌",
    "Zero announcements this hour. Clean slate! 🧹",
]

def send_announcements_to_discord(webhook: str, subject: dict, posts: list) -> None:
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    color   = subject["color"]

    # ── No new posts ─────────────────────────────────────────────────────────
    if not posts:
        if RUN_MODE == "auto":
            hour_seed = int(datetime.now().strftime("%H"))
            msg = ALL_CLEAR_MESSAGES[hour_seed % len(ALL_CLEAR_MESSAGES)]
            _flush(webhook, [{
                "title":       f"{subject['emoji']} {subject['name']}",
                "description": msg,
                "color":       0x57F287,
                "footer":      {"text": f"LMS Notifier • {now_str}"},
                "timestamp":   datetime.utcnow().isoformat() + "Z",
            }])
        elif RUN_MODE == "today":
            _flush(webhook, [{
                "title":       f"{subject['emoji']} {subject['name']}",
                "description": "✅ No announcements posted today.",
                "color":       0x95A5A6,
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
        print(f"  🔕 {subject['code']}: nothing new — sent all-clear.")
        return

    # ── Header ────────────────────────────────────────────────────────────────
    if RUN_MODE == "auto":
        window = "past 1 hour"
    elif RUN_MODE == "today":
        window = "today"
    else:
        window = f"past {LOOKBACK_HOURS // 24} days"

    all_embeds = [{
        "title":       f"{subject['emoji']} {subject['name']}",
        "description": f"📬 **{len(posts)} new announcement(s)** in the {window}.",
        "color":       color,
        "footer":      {"text": f"LMS Notifier • {now_str}"},
        "timestamp":   datetime.utcnow().isoformat() + "Z",
    }]

    for post in posts[:9]:
        meta_parts = []
        if post.get("author") not in ("Unknown", "", "—", None):
            meta_parts.append(f"👤 **{post['author']}**")
        if post.get("date_str") not in ("Unknown date", "", None):
            meta_parts.append(f"📅 {post['date_str']}")
        if post.get("replies") not in ("—", "", "0", None):
            r_val = post["replies"]
            meta_parts.append(f"💬 {r_val} repl{'y' if r_val == '1' else 'ies'}")

        meta_line = "  •  ".join(meta_parts)
        body      = post.get("body", "").strip()

        if body:
            full_desc = f"{meta_line}\n\n{body}" if meta_line else body
        else:
            full_desc = meta_line or "*(no body)*"

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
                "1. Check the **GitHub Actions log** for this run\n"
                "2. Find lines starting with `🌐 Trying:` to see which URLs were tried\n"
                "3. Find `Page snippet:` lines to see what each page returned\n"
                "4. Log into LMS manually → go to your attendance page → copy the URL\n"
                "5. Add it to `ATTENDANCE_URLS` at the top of `lms_scraper.py`"
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

    # Sort: lowest % first (most urgent at top)
    records.sort(key=lambda r: r["pct_float"] if r["pct_float"] is not None else 999)

    fields = []
    for rec in records[:25]:
        pct = rec["pct_float"]
        att = rec["attended"]
        tot = rec["total"]

        lines = [f"{rec['status']}  **{rec['percentage']}**  —  {att} / {tot} classes"]

        if pct is not None and pct < 75:
            needed = _classes_needed(att, tot)
            if needed: lines.append(f"┗ ⚠️ Need **{needed}** more to hit 75%")

        if pct is not None and pct >= 75:
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
        f"{overall}\n"
        f"\n"
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


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    if RUN_MODE == "auto":
        window = "last 1 hour"
    elif RUN_MODE == "today":
        window = "today (since midnight IST)"
    else:
        window = f"last {LOOKBACK_HOURS // 24} days"

    print("=" * 60)
    print("🎓  LMS Notifier — SIU Hyderabad")
    print(f"⚙️   Mode     : {RUN_MODE.upper()}")
    print(f"📅  Lookback : {window}")
    print(f"🕐  Run time : {datetime.now().strftime('%d %b %Y  %H:%M:%S')}")
    print("=" * 60)

    # ── Announcements ─────────────────────────────────────────────────────────
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

    # ── Attendance ─────────────────────────────────────────────────────────────
    # Always run in auto/today; optional in manual
    run_attendance = (RUN_MODE in ("auto", "today")) or (
        os.environ.get("FETCH_ATTENDANCE", "false").lower() == "true"
    )

    if run_attendance:
        print("\n📋 Scraping attendance (fresh login)…")
        try:
            att_session = login_to_lms(warm_up=True)
            att_data    = fetch_attendance(att_session)
            count       = len(att_data["records"]) if att_data else 0
            print(f"  🔍 {count} subject records found.")
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
