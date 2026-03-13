"""
LMS Notifier — SIU Hyderabad  v5
==================================
NEW in v5:
  - Assignments: posted date, due date → subject channel + deadlines digest
  - Quizzes: open/close time → subject channel + deadlines digest
  - New files/resources: filename + download link → subject channel
  - Grades: marks posted → subject channel
  - #deadlines channel: daily digest of everything due in next 7 days
  - Dedup cache: announcements, assignments, quizzes, files, grades all
    tracked — nothing ever gets re-sent between runs
  - Attendance: only sends when percentages change or someone crosses 75%
  - Attendance: @everyone ping when you drop below 75%
  - Attendance: strips "(CSE_II SEM)" clutter from subject names
  - Timetable-aware: AUTO mode skips overnight hours (11pm–7am IST)
  - Discord retry: 3 attempts with backoff on webhook failures
"""

import os
import re
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from pathlib import Path

# ─────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────
LMS_BASE      = "https://lmssithyd.siu.edu.in"
LMS_LOGIN_URL = f"{LMS_BASE}/login/index.php"

ATTENDANCE_URL     = f"{LMS_BASE}/attendance-report/Student-Attendance/index.php"
ATTENDANCE_TIMEOUT = 20
REQUEST_TIMEOUT    = 15

# IST hours during which AUTO mode will actually run (7am–11pm)
AUTO_ACTIVE_HOURS = range(7, 23)

LMS_USERNAME  = os.environ["LMS_USERNAME"]
LMS_PASSWORD  = os.environ["LMS_PASSWORD"]

RUN_MODE      = os.environ.get("RUN_MODE", "manual").lower()
LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", 7))
LOOKBACK_HOURS = 1 if RUN_MODE == "auto" else LOOKBACK_DAYS * 24

EMBED_DESC_LIMIT   = 4000
EMBEDS_PER_PAYLOAD = 10
CACHE_FILE         = Path("cache.json")

SUBJECTS = [
    {"name": "Career Essentials",                               "code": "CE",   "forum_id": "1942", "course_id": "", "webhook": os.environ["WEBHOOK_CAREER_ESSENTIALS"],  "emoji": "💼", "color": 0x5865F2},
    {"name": "Computer Architecture and Organization",          "code": "CAO",  "forum_id": "1937", "course_id": "", "webhook": os.environ["WEBHOOK_COMPUTER_ARCH"],      "emoji": "🖥️", "color": 0xEB459E},
    {"name": "Creative Thinking",                               "code": "CT",   "forum_id": "1941", "course_id": "", "webhook": os.environ["WEBHOOK_CREATIVE_THINKING"],  "emoji": "🎨", "color": 0xFEE75C},
    {"name": "Exploratory Data Analysis",                       "code": "EDA",  "forum_id": "1935", "course_id": "", "webhook": os.environ["WEBHOOK_EDA"],                "emoji": "📊", "color": 0x57F287},
    {"name": "Introduction to Environment and Sustainability",  "code": "IES",  "forum_id": "1936", "course_id": "", "webhook": os.environ["WEBHOOK_ENV_SUSTAIN"],        "emoji": "🌿", "color": 0x2ECC71},
    {"name": "Linear Algebra",                                  "code": "LA",   "forum_id": "1933", "course_id": "", "webhook": os.environ["WEBHOOK_LINEAR_ALGEBRA"],     "emoji": "📐", "color": 0x9B59B6},
    {"name": "Microcontrollers and Sensors",                    "code": "MCS",  "forum_id": "1934", "course_id": "", "webhook": os.environ["WEBHOOK_MICROCONTROLLERS"],   "emoji": "🔌", "color": 0xE67E22},
    {"name": "Python Programming",                              "code": "PY",   "forum_id": "1939", "course_id": "", "webhook": os.environ["WEBHOOK_PYTHON"],             "emoji": "🐍", "color": 0x3498DB},
    {"name": "Software Engineering",                            "code": "SE",   "forum_id": "1938", "course_id": "", "webhook": os.environ["WEBHOOK_SOFTWARE_ENG"],       "emoji": "⚙️", "color": 0xE74C3C},
    {"name": "Technical and Professional Communication Skills", "code": "TPCS", "forum_id": "1940", "course_id": "", "webhook": os.environ["WEBHOOK_TPCS"],               "emoji": "📝", "color": 0x1ABC9C},
]

ATTENDANCE_WEBHOOK = os.environ.get("WEBHOOK_ATTENDANCE", "")
DEADLINES_WEBHOOK  = os.environ.get("WEBHOOK_DEADLINES", "")

ALL_CLEAR_MSGS = [
    "You're all caught up! No new posts this hour. ✨",
    "Nothing new this hour — keep up the good work! 💪",
    "All quiet on the LMS front this hour. 🎉",
    "No new posts this hour. Relax, you're on top of it! 😌",
    "Zero announcements this hour. Clean slate! 🧹",
]


# ─────────────────────────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────────────────────────
def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text())
            for key in ("announcements", "assignments", "quizzes", "files", "grades"):
                data.setdefault(key, [])
            data.setdefault("attendance", {})
            return data
        except Exception:
            pass
    return {"announcements": [], "assignments": [], "quizzes": [],
            "files": [], "grades": [], "attendance": {}}

def save_cache(cache: dict) -> None:
    CACHE_FILE.write_text(json.dumps(cache, indent=2))
    print("  💾 Cache saved.")

def is_seen(cache: dict, key: str, uid: str) -> bool:
    return uid in cache.get(key, [])

def mark_seen(cache: dict, key: str, uid: str) -> None:
    if uid not in cache[key]:
        cache[key].append(uid)


# ─────────────────────────────────────────────────────────────────
# TIMETABLE GATE
# ─────────────────────────────────────────────────────────────────
def is_active_hour() -> bool:
    ist_hour = (datetime.utcnow() + timedelta(hours=5, minutes=30)).hour
    return ist_hour in AUTO_ACTIVE_HOURS


# ─────────────────────────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────────────────────────
def login_to_lms(warm_up: bool = False) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
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
        raise RuntimeError("❌ LMS login failed — wrong credentials?")

    logged_in = (
        "/my/" in r.url or "logout" in r.text.lower()
        or soup_after.find("a", {"data-title": "logout,moodle"}) is not None
        or soup_after.find(attrs={"class": lambda c: c and "usermenu" in c}) is not None
        or soup_after.find("div", {"id": "page-my-index"}) is not None
    )
    print(f"  {'✅ Logged in.' if logged_in else '⚠️  Login unclear — proceeding anyway.'}")

    if warm_up:
        print("  🔥 Warm-up: visiting dashboard...")
        try: session.get(f"{LMS_BASE}/my/", timeout=REQUEST_TIMEOUT)
        except Exception as ex: print(f"  ⚠️  Dashboard warm-up failed: {ex}")
        print("  🔥 Warm-up: priming attendance URL...")
        try: session.get(ATTENDANCE_URL, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        except Exception as ex: print(f"  ⚠️  Attendance prime failed: {ex}")

    return session

def _is_login_page(soup: BeautifulSoup, url: str) -> bool:
    return (
        "login/index.php" in url
        or soup.find("input", {"name": "logintoken"}) is not None
        or soup.find(id="loginerrormessage") is not None
    )


# ─────────────────────────────────────────────────────────────────
# DATE HELPERS
# ─────────────────────────────────────────────────────────────────
MOODLE_FORMATS = [
    "%d %B %Y, %I:%M %p", "%d %b %Y, %I:%M %p",
    "%A, %d %B %Y, %I:%M %p", "%d/%m/%Y, %I:%M %p",
    "%d %B %Y", "%d %b %Y", "%A, %d %B %Y",
]

def parse_moodle_date(raw: str) -> datetime | None:
    if not raw: return None
    raw = raw.strip()
    now = datetime.now()
    if raw.lower().startswith("today"):     return now
    if raw.lower().startswith("yesterday"): return now - timedelta(days=1)
    iso = re.match(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", raw)
    if iso:
        try: return datetime.strptime(iso.group(1), "%Y-%m-%dT%H:%M:%S")
        except: pass
    for fmt in MOODLE_FORMATS:
        try: return datetime.strptime(raw[:len(fmt)+6].strip(), fmt)
        except ValueError: continue
    m = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", raw)
    if m:
        try: return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y")
        except: pass
    return None

def fmt_date(d: datetime | None) -> str:
    return d.strftime("%d %b %Y, %I:%M %p") if d else "No date"

def days_until(d: datetime | None) -> int | None:
    if not d: return None
    return (d.date() - datetime.now().date()).days


# ─────────────────────────────────────────────────────────────────
# COURSE ID DISCOVERY
# ─────────────────────────────────────────────────────────────────
def discover_course_ids(session: requests.Session) -> None:
    print("  🔍 Discovering course IDs from dashboard...")
    try:
        r    = session.get(f"{LMS_BASE}/my/", timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=re.compile(r"/course/view\.php\?id=\d+")):
            href = a.get("href", "")
            m = re.search(r"id=(\d+)", href)
            if not m: continue
            cid  = m.group(1)
            name = a.get_text(strip=True).lower()
            for subj in SUBJECTS:
                if not subj["course_id"]:
                    words = subj["name"].lower().split()[:3]
                    if any(w in name for w in words):
                        subj["course_id"] = cid
                        print(f"    ✅ {subj['code']} → course_id={cid}")
                        break
    except Exception as ex:
        print(f"  ⚠️  Course ID discovery failed: {ex}")


# ─────────────────────────────────────────────────────────────────
# BODY EXTRACTOR
# ─────────────────────────────────────────────────────────────────
def _extract_body(tag) -> str:
    if tag is None: return ""
    for t in tag.find_all(["img", "script", "style", "figure"]): t.decompose()
    lines = []
    for el in tag.descendants:
        if hasattr(el, "name"): continue
        text = str(el).strip()
        if not text: continue
        pname = el.parent.name if el.parent else ""
        if pname in ("p","div","li","h1","h2","h3","h4","h5","h6","td","th"):
            if lines and lines[-1] != "\n": lines.append("\n")
        lines.append(text)
        if pname == "br": lines.append("\n")
    body = "".join(lines)
    return re.sub(r"\n{3,}", "\n\n", body).strip()


# ─────────────────────────────────────────────────────────────────
# ANNOUNCEMENT SCRAPER
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
            if date_obj: date_str = fmt_date(date_obj); break
    if not date_obj:
        for td in row.find_all("td"):
            d = parse_moodle_date(td.get_text(" ", strip=True))
            if d: date_obj, date_str = d, fmt_date(d); break

    author_tag = row.select_one("td.author a") or row.select_one(".author a")
    author     = author_tag.get_text(strip=True) if author_tag else "Unknown"
    rep_td     = row.select_one("td.replies")
    replies    = rep_td.get_text(strip=True) if rep_td else "—"
    return {"title": title, "url": post_url, "date": date_obj,
            "date_str": date_str, "author": author, "replies": replies}


def fetch_post_full(session: requests.Session, post_url: str) -> tuple[str, str, str]:
    try:
        r    = session.get(post_url, timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")

        author = "Unknown"
        for sel in ["address.author a", ".forumpost .author a",
                    "[data-region='post'] .username", ".author a", ".username a", ".fullname"]:
            tag = soup.select_one(sel)
            if tag: author = tag.get_text(strip=True); break

        date_str = "Unknown date"
        for sel in ["address.author time", ".forumpost .author time", "time[datetime]",
                    ".forumpost .date", "[data-region='post'] .date", ".posted"]:
            tag = soup.select_one(sel)
            if tag:
                raw = tag.get("datetime", "") or tag.get_text(strip=True)
                d   = parse_moodle_date(raw)
                if d: date_str = fmt_date(d); break
                elif raw.strip(): date_str = raw.strip()[:40]; break

        body = ""
        for sel in [".posting", "[data-region='post-content-container']",
                    ".post-content-container", ".forumpost .content .no-overflow",
                    ".forumpost .posting", ".forumpost .post-content", "div.message",
                    ".post-content", "article.forumpost", "[data-region='post']", ".forumpost"]:
            tag = soup.select_one(sel)
            if tag:
                c = _extract_body(tag)
                if len(c) > 20: body = c; break

        if not body:
            main = (soup.select_one("#region-main") or soup.select_one("div[role='main']")
                    or soup.select_one("main") or soup.find("body"))
            if main:
                for noise in main.find_all(["nav","header","footer","aside","form","script","style"]):
                    noise.decompose()
                for ns in [".breadcrumb","#page-header",".header",".reply",".discussion-nav"]:
                    for el in main.select(ns): el.decompose()
                body = _extract_body(main)
                if not body or len(body) < 20:
                    body = re.sub(r"\s+", " ", main.get_text(" ", strip=True)).strip()

        # Strip Moodle UI chrome
        idx = body.find("Settings Star this discussion")
        if idx != -1: body = body[idx + len("Settings Star this discussion"):].lstrip(" \n")
        if body.startswith("Star this discussion"):
            body = body[len("Star this discussion"):].lstrip(" \n")
        for suffix in ["Permalink", " Reply", " Edit", " Delete", "Export to portfolio"]:
            if body.endswith(suffix): body = body[:-len(suffix)].rstrip(" \n")

        return author, date_str, body.strip()
    except Exception as ex:
        print(f"      ⚠️  Could not fetch post: {ex}")
        return "Unknown", "Unknown date", ""


def fetch_announcements(session: requests.Session, forum_id: str, lookback_hours: int) -> list:
    url    = f"{LMS_BASE}/mod/forum/view.php?id={forum_id}"
    cutoff = datetime.now() - timedelta(hours=lookback_hours)
    r      = session.get(url, timeout=REQUEST_TIMEOUT)
    soup   = BeautifulSoup(r.text, "html.parser")
    print(f"    📄 {soup.title.string.strip() if soup.title else 'N/A'}")

    rows = (soup.select("tr.discussion") or soup.select("table.forumheaderlist tr")[1:]
            or soup.select(".discussion-list .discussion") or [])
    print(f"    🔎 Strategy A rows: {len(rows)}")

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
                             "date_str": "Unknown date", "author": "Unknown", "replies": "—", "body": ""})
        for post in results[:9]:
            author, date_str, body = fetch_post_full(session, post["url"])
            post.update({"author": author, "date_str": date_str, "body": body,
                         "date": parse_moodle_date(date_str) or datetime.min})
        if RUN_MODE == "auto":
            results = [p for p in results if p["date"] >= cutoff]
        results.sort(key=lambda x: x["date"], reverse=True)
        return results

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

    for post in results[:9]:
        author, date_str, body = fetch_post_full(session, post["url"])
        post["body"] = body
        if author != "Unknown":        post["author"]   = author
        if date_str != "Unknown date": post["date_str"] = date_str

    results.sort(key=lambda x: (x["date"] or datetime.min), reverse=True)
    return results


# ─────────────────────────────────────────────────────────────────
# ASSIGNMENT SCRAPER
# ─────────────────────────────────────────────────────────────────
def fetch_assignments(session: requests.Session, course_id: str) -> list:
    if not course_id: return []
    try:
        r    = session.get(f"{LMS_BASE}/mod/assign/index.php?id={course_id}", timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for row in soup.select("table tr"):
            link = row.select_one("a[href*='mod/assign/view.php']")
            if not link: continue
            title = link.get_text(strip=True)
            href  = link.get("href", "")
            if not href.startswith("http"): href = LMS_BASE + href
            due_date, due_str = None, "No due date"
            for cell in row.find_all("td"):
                d = parse_moodle_date(cell.get_text(strip=True))
                if d and d > datetime.now() - timedelta(days=1):
                    due_date, due_str = d, fmt_date(d); break
            results.append({"title": title, "url": href, "due_date": due_date, "due_str": due_str})
        print(f"    📋 Assignments: {len(results)}")
        return results
    except Exception as ex:
        print(f"    ⚠️  Assignment fetch failed: {ex}"); return []


# ─────────────────────────────────────────────────────────────────
# QUIZ SCRAPER
# ─────────────────────────────────────────────────────────────────
def fetch_quizzes(session: requests.Session, course_id: str) -> list:
    if not course_id: return []
    try:
        r    = session.get(f"{LMS_BASE}/mod/quiz/index.php?id={course_id}", timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for row in soup.select("table tr"):
            link = row.select_one("a[href*='mod/quiz/view.php']")
            if not link: continue
            title = link.get_text(strip=True)
            href  = link.get("href", "")
            if not href.startswith("http"): href = LMS_BASE + href
            dates = [parse_moodle_date(c.get_text(strip=True)) for c in row.find_all("td")]
            dates = [d for d in dates if d]
            open_date  = dates[0] if len(dates) > 0 else None
            close_date = dates[1] if len(dates) > 1 else dates[0] if dates else None
            results.append({"title": title, "url": href, "open_date": open_date,
                             "close_date": close_date, "close_str": fmt_date(close_date)})
        print(f"    🧪 Quizzes: {len(results)}")
        return results
    except Exception as ex:
        print(f"    ⚠️  Quiz fetch failed: {ex}"); return []


# ─────────────────────────────────────────────────────────────────
# FILES SCRAPER
# ─────────────────────────────────────────────────────────────────
def fetch_files(session: requests.Session, course_id: str) -> list:
    if not course_id: return []
    try:
        r    = session.get(f"{LMS_BASE}/course/view.php?id={course_id}", timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for a in soup.select("a[href*='/mod/resource/view.php'], a[href*='/mod/folder/view.php']"):
            href  = a.get("href", "")
            if not href.startswith("http"): href = LMS_BASE + href
            title = a.get_text(strip=True)
            if not title: continue
            ftype = "📁 Folder" if "folder" in href else "📄 File"
            results.append({"title": title, "url": href, "type": ftype})
        print(f"    📁 Files: {len(results)}")
        return results
    except Exception as ex:
        print(f"    ⚠️  File fetch failed: {ex}"); return []


# ─────────────────────────────────────────────────────────────────
# GRADES SCRAPER
# ─────────────────────────────────────────────────────────────────
def fetch_grades(session: requests.Session, course_id: str) -> list:
    if not course_id: return []
    try:
        r    = session.get(f"{LMS_BASE}/grade/report/user/index.php?id={course_id}", timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        results = []
        for row in soup.select("table.user-grade tr"):
            cells = row.find_all("td")
            if len(cells) < 2: continue
            item  = cells[0].get_text(strip=True)
            grade = cells[1].get_text(strip=True) if len(cells) > 1 else "—"
            if not item or grade in ("—", "", "-"): continue
            if re.search(r"course total|category total", item, re.I): continue
            feedback = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            results.append({"item": item, "grade": grade, "feedback": feedback,
                             "url": f"{LMS_BASE}/grade/report/user/index.php?id={course_id}"})
        print(f"    🎯 Grades: {len(results)}")
        return results
    except Exception as ex:
        print(f"    ⚠️  Grade fetch failed: {ex}"); return []


# ─────────────────────────────────────────────────────────────────
# ATTENDANCE SCRAPER
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
        p, t = int(re.sub(r"\D","",present)), int(re.sub(r"\D","",total))
        if t == 0: return None
        if p/t >= target/100: return 0
        n = 0
        while (p+n)/(t+n) < target/100: n += 1
        return n
    except: return None

def _classes_can_skip(present: str, total: str, target: float = 75.0) -> int | None:
    try:
        p, t = int(re.sub(r"\D","",present)), int(re.sub(r"\D","",total))
        if t == 0 or p/t < target/100: return 0
        s = 0
        while t+s+1 > 0 and p/(t+s+1) >= target/100: s += 1
        return s
    except: return None

def _clean_subject_name(name: str) -> str:
    return re.sub(r"\s*\([A-Z_\s]+SEM\)", "", name).strip()

def _parse_attendance_table(soup: BeautifulSoup) -> dict | None:
    table = soup.find("table")
    if not table:
        print("  ⚠️  No <table> found on page."); return None

    rows = table.find_all("tr")
    print(f"  📋 Table has {len(rows)} rows total")
    if not rows: return None

    records = []
    total_conducted = total_attended = total_pct = None

    for tr in rows:
        cells = [c.get_text(strip=True) for c in tr.find_all(["td","th"])]
        if not cells: continue
        first = cells[0].strip()
        if any(k in first.lower() for k in ("course","subject","name")):
            print(f"  📋 Header: {cells}"); continue
        row_text = " ".join(cells)
        if re.search(r"total|grand|percentage", row_text, re.I) and "CSE" not in row_text:
            m = re.search(r"total conducted[^\d]*(\d+)", row_text, re.I)
            if m: total_conducted = int(m.group(1))
            m = re.search(r"total attended[^\d]*(\d+)", row_text, re.I)
            if m: total_attended = int(m.group(1))
            m = re.search(r"([\d.]+)\s*%", row_text)
            if m: total_pct = float(m.group(1))
            continue
        if len(cells) < 4: continue
        subj     = cells[0]
        total    = cells[1]
        attended = cells[3] if len(cells) > 3 else cells[2]
        pct_raw  = cells[4] if len(cells) > 4 else cells[-1]
        pct = _pct_float(pct_raw)
        if pct is None:
            t_f, a_f = _pct_float(total), _pct_float(attended)
            if t_f and a_f and t_f > 0: pct = round(a_f/t_f*100, 2)
        records.append({
            "subject":    _clean_subject_name(subj),
            "attended":   attended, "total": total,
            "percentage": f"{pct:.1f}%" if pct is not None else pct_raw,
            "pct_float":  pct, "status": _status(pct),
        })
        print(f"  ✅ {_clean_subject_name(subj)[:35]} → {attended}/{total} = {pct_raw}")

    if not records: return None
    return {"records": records, "total_conducted": total_conducted,
            "total_attended": total_attended, "total_pct": total_pct}

def fetch_attendance(session: requests.Session) -> dict | None:
    print(f"  🌐 Fetching: {ATTENDANCE_URL}")
    try:
        r    = session.get(ATTENDANCE_URL, timeout=ATTENDANCE_TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        if _is_login_page(soup, r.url):
            print("  ⚠️  Redirected to login — retrying with fresh session...")
            fresh = login_to_lms(warm_up=True)
            r    = fresh.get(ATTENDANCE_URL, timeout=ATTENDANCE_TIMEOUT)
            soup = BeautifulSoup(r.text, "html.parser")
            if _is_login_page(soup, r.url):
                print("  ❌ Still on login page."); return None
        data = _parse_attendance_table(soup)
        if data: return data
        snippet = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))[:400]
        print(f"  ⚠️  No table. Snippet: {snippet}")
        print(f"  🔗  Final URL: {r.url}")
        return None
    except Exception as ex:
        import traceback; print(f"  ❌ {ex}"); traceback.print_exc(); return None


# ─────────────────────────────────────────────────────────────────
# DISCORD HELPERS
# ─────────────────────────────────────────────────────────────────
def _post_webhook(webhook: str, payload: dict) -> None:
    if not webhook: return
    import time
    for attempt in range(3):
        try:
            r = requests.post(webhook, json=payload, timeout=10)
            if r.status_code in (200, 204): return
            if r.status_code == 429: time.sleep(2); continue
            print(f"    ❌ Discord {r.status_code}: {r.text[:200]}"); return
        except Exception as ex:
            print(f"    ⚠️  Webhook attempt {attempt+1}: {ex}")

def _flush(webhook: str, embeds: list) -> None:
    for i in range(0, len(embeds), EMBEDS_PER_PAYLOAD):
        _post_webhook(webhook, {"embeds": embeds[i:i+EMBEDS_PER_PAYLOAD]})

def _chunk_text(text: str, limit: int = EMBED_DESC_LIMIT) -> list[str]:
    if len(text) <= limit: return [text]
    chunks = []
    while text:
        if len(text) <= limit: chunks.append(text); break
        cut = text.rfind("\n\n",0,limit)
        if cut==-1: cut=text.rfind("\n",0,limit)
        if cut==-1: cut=text.rfind(". ",0,limit)
        if cut==-1: cut=text.rfind(" ",0,limit)
        if cut==-1: cut=limit
        else: cut+=1
        chunks.append(text[:cut].rstrip())
        text=text[cut:].lstrip()
    return chunks


# ─────────────────────────────────────────────────────────────────
# DISCORD SENDERS
# ─────────────────────────────────────────────────────────────────
def send_announcements_to_discord(webhook: str, subject: dict, posts: list) -> None:
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    color   = subject["color"]

    if not posts:
        if RUN_MODE == "auto":
            msg = ALL_CLEAR_MSGS[int(datetime.now().strftime("%H")) % len(ALL_CLEAR_MSGS)]
            _flush(webhook, [{"title": f"{subject['emoji']} {subject['name']}", "description": msg,
                               "color": 0x57F287, "footer": {"text": f"LMS Notifier • {now_str}"},
                               "timestamp": datetime.utcnow().isoformat()+"Z"}])
        else:
            _flush(webhook, [{"title": f"{subject['emoji']} {subject['name']}",
                               "description": f"✅ No announcements in the past {LOOKBACK_DAYS} day(s).",
                               "color": 0x95A5A6, "footer": {"text": f"LMS Notifier • {now_str}"},
                               "timestamp": datetime.utcnow().isoformat()+"Z"}])
        print(f"  📭 {subject['code']}: nothing new.")
        return

    window = "past 1 hour" if RUN_MODE == "auto" else f"past {LOOKBACK_DAYS} days"
    all_embeds = [{"title": f"{subject['emoji']} {subject['name']}",
                   "description": f"📬 **{len(posts)}** new announcement(s) in the {window}.",
                   "color": color, "footer": {"text": f"LMS Notifier • {now_str}"},
                   "timestamp": datetime.utcnow().isoformat()+"Z"}]

    for post in posts[:9]:
        meta_parts = []
        if post.get("author") not in ("Unknown","","—",None):
            meta_parts.append(f"👤 **{post['author']}**")
        if post.get("date_str") not in ("Unknown date","",None):
            meta_parts.append(f"📅 {post['date_str']}")
        if post.get("replies") not in ("—","","0",None):
            rv = post["replies"]
            meta_parts.append(f"💬 {rv} repl{'y' if rv=='1' else 'ies'}")
        meta_line = "  •  ".join(meta_parts)
        body      = post.get("body","").strip()
        full_desc = f"{meta_line}\n\n{body}" if meta_line and body else body or meta_line or "*(no content)*"
        chunks    = _chunk_text(full_desc)
        for idx, chunk in enumerate(chunks):
            suffix = "" if idx==0 else f" *(cont. {idx+1}/{len(chunks)})*"
            all_embeds.append({
                "title": f"📢 {post['title']}{suffix}", "url": post["url"],
                "description": chunk, "color": color,
                "footer": {"text": f"{subject['emoji']} {subject['name']} • LMS Notifier"},
                "timestamp": datetime.utcnow().isoformat()+"Z",
            })

    _flush(webhook, all_embeds)
    print(f"  ✅ {subject['code']}: {len(posts)} post(s) sent.")


def _urgency_color(days: int | None, default: int) -> int:
    if days is None: return default
    if days <= 0: return 0xE74C3C
    if days <= 1: return 0xE74C3C
    if days <= 3: return 0xF39C12
    return default

def _due_label(days: int | None) -> str:
    if days is None: return ""
    if days < 0:  return f"  *(overdue by {abs(days)} day(s))*"
    if days == 0: return "  🚨 **DUE TODAY**"
    if days == 1: return "  ⚠️ **Tomorrow**"
    return f"  *({days} days away)*"


def send_assignments_to_discord(webhook: str, subject: dict, assignments: list, cache: dict) -> list:
    new_ones = []
    now_str  = datetime.now().strftime("%d %b %Y, %I:%M %p")
    for a in assignments:
        if is_seen(cache, "assignments", a["url"]): continue
        mark_seen(cache, "assignments", a["url"])
        new_ones.append({**a, "subject": subject["name"], "emoji": subject["emoji"], "code": subject["code"]})
        d = days_until(a["due_date"])
        due_line = f"⏰ **Due:** {a['due_str']}{_due_label(d)}"
        _post_webhook(webhook, {"embeds": [{
            "title": f"📝 New Assignment — {a['title']}", "url": a["url"],
            "description": due_line, "color": _urgency_color(d, subject["color"]),
            "footer": {"text": f"{subject['emoji']} {subject['name']} • LMS Notifier • {now_str}"},
            "timestamp": datetime.utcnow().isoformat()+"Z",
        }]})
        print(f"  ✅ Assignment: {a['title']}")
    return new_ones


def send_quizzes_to_discord(webhook: str, subject: dict, quizzes: list, cache: dict) -> list:
    new_ones = []
    now_str  = datetime.now().strftime("%d %b %Y, %I:%M %p")
    for q in quizzes:
        if is_seen(cache, "quizzes", q["url"]): continue
        mark_seen(cache, "quizzes", q["url"])
        new_ones.append({**q, "subject": subject["name"], "emoji": subject["emoji"], "code": subject["code"]})
        d     = days_until(q["close_date"])
        lines = []
        if q["open_date"]:  lines.append(f"🟢 **Opens:** {fmt_date(q['open_date'])}")
        if q["close_date"]: lines.append(f"🔴 **Closes:** {q['close_str']}{_due_label(d)}")
        _post_webhook(webhook, {"embeds": [{
            "title": f"🧪 Quiz Scheduled — {q['title']}", "url": q["url"],
            "description": "\n".join(lines) or "No timing info available.",
            "color": _urgency_color(d, 0x9B59B6),
            "footer": {"text": f"{subject['emoji']} {subject['name']} • LMS Notifier • {now_str}"},
            "timestamp": datetime.utcnow().isoformat()+"Z",
        }]})
        print(f"  ✅ Quiz: {q['title']}")
    return new_ones


def send_files_to_discord(webhook: str, subject: dict, files: list, cache: dict) -> None:
    now_str   = datetime.now().strftime("%d %b %Y, %I:%M %p")
    new_files = [f for f in files if not is_seen(cache,"files",f["url"])]
    for f in new_files: mark_seen(cache,"files",f["url"])
    if not new_files: return
    embeds = [{"title": f"{f['type']} — {f['title']}", "url": f["url"],
                "description": f"A new file was uploaded to **{subject['name']}**.",
                "color": subject["color"],
                "footer": {"text": f"{subject['emoji']} {subject['name']} • LMS Notifier • {now_str}"},
                "timestamp": datetime.utcnow().isoformat()+"Z"}
               for f in new_files]
    _flush(webhook, embeds)
    print(f"  ✅ Files: {len(new_files)} new")


def send_grades_to_discord(webhook: str, subject: dict, grades: list, cache: dict) -> None:
    now_str    = datetime.now().strftime("%d %b %Y, %I:%M %p")
    new_grades = []
    for g in grades:
        uid = f"{subject['code']}::{g['item']}"
        if is_seen(cache,"grades",uid): continue
        mark_seen(cache,"grades",uid)
        new_grades.append(g)
    if not new_grades: return
    fields = [{"name": g["item"],
               "value": f"**{g['grade']}**" + (f"\n*{g['feedback']}*" if g["feedback"] else ""),
               "inline": True} for g in new_grades]
    _post_webhook(webhook, {"embeds": [{
        "title": f"🎯 Grades Posted — {subject['name']}",
        "description": f"**{len(new_grades)}** new grade(s) are available!",
        "color": 0xF1C40F, "fields": fields[:25],
        "footer": {"text": f"{subject['emoji']} {subject['name']} • LMS Notifier • {now_str}"},
        "timestamp": datetime.utcnow().isoformat()+"Z",
    }]})
    print(f"  ✅ Grades: {len(new_grades)} new")


def send_deadlines_digest(all_deadlines: list) -> None:
    """Daily digest of everything due in the next 7 days across all subjects."""
    if not DEADLINES_WEBHOOK:
        print("⚠️  WEBHOOK_DEADLINES not set — skipping digest."); return

    now_str  = datetime.now().strftime("%d %b %Y, %I:%M %p")
    today    = datetime.now().date()
    upcoming = []

    for item in all_deadlines:
        due = item.get("due_date") or item.get("close_date")
        if not due: continue
        d = (due.date() - today).days
        if -1 <= d <= 7:
            upcoming.append({**item, "_days": d})

    if not upcoming:
        _post_webhook(DEADLINES_WEBHOOK, {"embeds": [{
            "title": "📅 Deadlines Digest",
            "description": "🎉 Nothing due in the next 7 days. You're all clear!",
            "color": 0x2ECC71,
            "footer": {"text": f"LMS Notifier • {now_str}"},
            "timestamp": datetime.utcnow().isoformat()+"Z",
        }]})
        print("📅 Deadlines digest: clear!"); return

    upcoming.sort(key=lambda x: x["_days"])
    fields = []
    for item in upcoming[:25]:
        d    = item["_days"]
        due  = item.get("due_date") or item.get("close_date")
        subj = item.get("subject","")
        emoji = item.get("emoji","📌")
        kind  = "🧪 Quiz" if "open_date" in item else "📝 Assignment"
        name  = item.get("title","Untitled")
        if d < 0:    urgency = f"🔴 OVERDUE by {abs(d)} day(s)"
        elif d == 0: urgency = "🚨 DUE TODAY"
        elif d == 1: urgency = "⚠️ Tomorrow"
        else:        urgency = f"📅 In {d} days"
        fields.append({
            "name":   f"{emoji} [{item.get('code','')}] {name}",
            "value":  f"{urgency}  •  {fmt_date(due)}",
            "inline": False,
        })

    overdue = sum(1 for x in upcoming if x["_days"] < 0)
    today_c = sum(1 for x in upcoming if x["_days"] == 0)
    soon    = sum(1 for x in upcoming if 1 <= x["_days"] <= 3)
    color   = 0xE74C3C if overdue or today_c else (0xF39C12 if soon else 0x3498DB)

    parts = [f"📌 **{len(upcoming)} deadline(s)** in the next 7 days"]
    if overdue: parts.append(f"🔴 {overdue} overdue")
    if today_c: parts.append(f"🚨 {today_c} due today")
    if soon:    parts.append(f"⚠️ {soon} due within 3 days")

    # @everyone ping if anything is due today or overdue
    payload = {"embeds": [{"title": "📅 Deadlines Digest — SIU Hyderabad",
                            "description": "  •  ".join(parts),
                            "color": color, "fields": fields,
                            "footer": {"text": f"LMS Notifier • {now_str}"},
                            "timestamp": datetime.utcnow().isoformat()+"Z"}]}
    if overdue or today_c:
        payload["content"] = "@everyone"
    _post_webhook(DEADLINES_WEBHOOK, payload)
    print(f"📅 Deadlines digest: {len(upcoming)} item(s).")


def send_attendance_to_discord(data: dict | None, cache: dict) -> None:
    if not ATTENDANCE_WEBHOOK:
        print("⚠️  WEBHOOK_ATTENDANCE not set — skipping."); return

    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")

    if data is None:
        _post_webhook(ATTENDANCE_WEBHOOK, {"embeds": [{
            "title": "📋 Attendance — Fetch Failed",
            "description": (
                "❌ Could not scrape the attendance page.\n\n"
                "**How to fix:**\n"
                "1. Check GitHub Actions log for error messages\n"
                "2. Log into LMS manually → open attendance page → copy the URL\n"
                "3. Update `ATTENDANCE_URL` at the top of `lms_scraper.py`"
            ),
            "color": 0xE74C3C,
            "footer": {"text": f"Attendance Bot • SIU Hyderabad • {now_str}"},
            "timestamp": datetime.utcnow().isoformat()+"Z",
        }]}); return

    records         = data["records"]
    total_conducted = data.get("total_conducted")
    total_attended  = data.get("total_attended")
    total_pct       = data.get("total_pct")

    # Only send in AUTO mode if something changed
    last_pcts      = cache.get("attendance", {})
    changed        = False
    danger_crossed = []
    for rec in records:
        subj = rec["subject"]
        cur  = rec["pct_float"]
        prev = last_pcts.get(subj)
        if prev is None or cur != prev: changed = True
        if cur is not None and prev is not None and prev >= 75 and cur < 75:
            danger_crossed.append(subj)

    if not changed and RUN_MODE == "auto":
        print("  ℹ️  Attendance unchanged — skipping send."); return

    cache["attendance"] = {r["subject"]: r["pct_float"] for r in records}

    records.sort(key=lambda r: r["pct_float"] if r["pct_float"] is not None else 999)
    fields = []
    for rec in records[:25]:
        pct  = rec["pct_float"]
        att  = rec["attended"]
        tot  = rec["total"]
        line1 = f"{rec['status']}  **{rec['percentage']}**  —  {att} / {tot} classes"
        lines = [line1]
        if pct is not None and pct < 75:
            needed = _classes_needed(att, tot)
            if needed: lines.append(f"┗ ⚠️ Need **{needed}** more to hit 75%")
        elif pct is not None and pct >= 75:
            skip = _classes_can_skip(att, tot)
            if skip and skip > 0: lines.append(f"┗ ✅ Can skip up to **{skip}** class(es)")
        fields.append({"name": rec["subject"][:50], "value": "\n".join(lines), "inline": False})

    pct_vals   = [r["pct_float"] for r in records if r["pct_float"] is not None]
    avg_pct    = sum(pct_vals)/len(pct_vals) if pct_vals else 0
    low_count  = sum(1 for p in pct_vals if p < 75)
    border     = sum(1 for p in pct_vals if 75 <= p < 85)
    safe_count = sum(1 for p in pct_vals if p >= 85)
    top_color  = 0xE74C3C if low_count else (0xF39C12 if border else 0x2ECC71)

    if total_conducted and total_attended:
        ov_pct  = total_pct or round(total_attended/total_conducted*100, 1)
        overall = f"📚 **{total_attended} / {total_conducted}** sessions  →  **{ov_pct:.1f}%** overall"
    else:
        overall = f"📊 Avg attendance: **{avg_pct:.1f}%**"

    alert = ""
    if danger_crossed:
        alert = "\n\n🚨 **JUST DROPPED BELOW 75%:**\n" + "\n".join(f"• {s}" for s in danger_crossed)

    summary = (f"{overall}\n\n"
               f"🔴 Low (<75%): **{low_count}**\n"
               f"🟡 Borderline (75–84%): **{border}**\n"
               f"🟢 Safe (≥85%): **{safe_count}**"
               f"{alert}")

    payload = {"embeds": [{"title": "📋 Attendance Report — SIU Hyderabad",
                            "description": summary, "color": top_color, "fields": fields,
                            "footer": {"text": f"Attendance Bot • SIU Hyderabad • {now_str}"},
                            "timestamp": datetime.utcnow().isoformat()+"Z"}]}
    if danger_crossed: payload["content"] = "@everyone"
    _post_webhook(ATTENDANCE_WEBHOOK, payload)
    print(f"✅ Attendance sent — {len(records)} subjects.")


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────
def main():
    now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
    print("=" * 60)
    print("🎓  LMS Notifier — SIU Hyderabad  v5")
    print(f"⚙️   Mode     : {RUN_MODE.upper()}")
    print(f"🕐  IST time  : {now_ist.strftime('%d %b %Y  %H:%M:%S')}")
    print("=" * 60)

    if RUN_MODE == "auto" and not is_active_hour():
        print(f"😴 Outside active hours (7am–11pm IST). Skipping this run.")
        return

    cache   = load_cache()
    session = login_to_lms()
    discover_course_ids(session)

    total_new     = 0
    all_deadlines = []

    for subject in SUBJECTS:
        print(f"\n📚 [{subject['code']}] {subject['name']}")
        cid = subject.get("course_id","")

        # Announcements
        try:
            posts     = fetch_announcements(session, subject["forum_id"], LOOKBACK_HOURS)
            new_posts = [p for p in posts if not is_seen(cache,"announcements",p["url"])]
            for p in new_posts: mark_seen(cache,"announcements",p["url"])
            print(f"  🔍 {len(new_posts)} new announcement(s).")
            send_announcements_to_discord(subject["webhook"], subject, new_posts)
            total_new += len(new_posts)
        except Exception as ex:
            import traceback; print(f"  ❌ Announcements: {ex}"); traceback.print_exc()

        # Assignments
        try:
            assignments = fetch_assignments(session, cid)
            new_a = send_assignments_to_discord(subject["webhook"], subject, assignments, cache)
            all_deadlines.extend(new_a)
            for a in assignments:
                d = days_until(a.get("due_date"))
                if d is not None and 0 <= d <= 7:
                    all_deadlines.append({**a,"subject":subject["name"],"emoji":subject["emoji"],"code":subject["code"]})
        except Exception as ex:
            import traceback; print(f"  ❌ Assignments: {ex}"); traceback.print_exc()

        # Quizzes
        try:
            quizzes = fetch_quizzes(session, cid)
            new_q = send_quizzes_to_discord(subject["webhook"], subject, quizzes, cache)
            all_deadlines.extend(new_q)
            for q in quizzes:
                d = days_until(q.get("close_date"))
                if d is not None and 0 <= d <= 7:
                    all_deadlines.append({**q,"subject":subject["name"],"emoji":subject["emoji"],"code":subject["code"]})
        except Exception as ex:
            import traceback; print(f"  ❌ Quizzes: {ex}"); traceback.print_exc()

        # Files
        try:
            send_files_to_discord(subject["webhook"], subject, fetch_files(session, cid), cache)
        except Exception as ex:
            import traceback; print(f"  ❌ Files: {ex}"); traceback.print_exc()

        # Grades
        try:
            send_grades_to_discord(subject["webhook"], subject, fetch_grades(session, cid), cache)
        except Exception as ex:
            import traceback; print(f"  ❌ Grades: {ex}"); traceback.print_exc()

    # Deadlines digest
    print("\n📅 Sending deadlines digest...")
    try:
        seen_urls, unique = set(), []
        for item in all_deadlines:
            u = item.get("url","")
            if u not in seen_urls:
                seen_urls.add(u); unique.append(item)
        send_deadlines_digest(unique)
    except Exception as ex:
        import traceback; print(f"  ❌ Digest: {ex}"); traceback.print_exc()

    # Attendance
    run_attendance = (RUN_MODE == "auto") or (os.environ.get("FETCH_ATTENDANCE","false").lower() == "true")
    if run_attendance:
        print("\n📋 Scraping attendance...")
        try:
            att_session = login_to_lms(warm_up=True)
            att_data    = fetch_attendance(att_session)
            print(f"  🔍 {len(att_data['records']) if att_data else 0} records.")
            send_attendance_to_discord(att_data, cache)
        except Exception as ex:
            import traceback; print(f"  ❌ Attendance: {ex}"); traceback.print_exc()
            send_attendance_to_discord(None, cache)

    save_cache(cache)
    print("\n" + "="*60)
    print(f"✅  Done — {total_new} new announcement(s).")
    print("="*60)


if __name__ == "__main__":
    main()
