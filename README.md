# 📢 SIT-LMS Notifier

> **Never miss a test, assignment, announcement, or grade again.**
> Get every LMS update delivered straight to your Discord subject channels — automatically, every hour.

---

## 🤔 What is this?

If you're a student at **Symbiosis Institute of Technology, Hyderabad**, you know the pain:
- You forget to check the LMS and miss an important announcement about a test
- A new assignment gets posted and you only notice it the night before it's due
- You lose track of your attendance and suddenly realise you're below 75%
- You check each subject one by one — 10 subjects, 10 clicks, every single day

This project fixes all of that. It's a **GitHub Actions bot** that logs into your LMS every hour and delivers everything to your Discord server automatically.

---

## ✨ What you get in Discord

Everything is posted to each subject's own channel. One channel per subject — all updates for that subject land there.

### 📢 Announcements
- Full announcement body (not a truncated preview — the whole thing)
- Author and timestamp
- Direct link to the LMS post
- Quiet "all clear" when nothing is new that hour

### 📝 Assignments
- Notified the moment a new assignment is posted
- Shows the due date with urgency coloring — 🔴 red if due today/tomorrow, 🟡 orange if within 3 days
- Daily reminder in the channel for anything due in the next 7 days

### 🧪 Quizzes
- Notified when a new quiz is scheduled
- Shows open and close times
- Same urgency coloring as assignments

### 📁 Files & Resources
- Notified whenever a new PDF, slide deck, or lab file is uploaded
- Direct download link included

### 🎯 Grades
- Notified as soon as marks are posted to your grade report
- Shows the item name, mark, and any feedback

### 📋 Attendance (every hour, only when something changes)
- Attended / Total sessions per subject
- Percentage, color-coded 🔴🟡🟢
- How many more classes you need to hit 75% (if below)
- How many classes you can safely skip (if above)
- **@everyone ping** if you drop below 75% in any subject

---

## 🗂️ Project Structure

```
sit-lms-notifier/
├── lms_scraper.py          # Main script — login, scrape, send to Discord
├── lms_notifier.yml        # GitHub Actions workflow (place in .github/workflows/)
├── cache.json              # Tracks already-sent items (auto-managed, never re-sends)
└── README.md               # This file
```

---

## 🚀 Setup Guide

This is a one-time setup that takes about **15–20 minutes**.

### Step 1 — Create the repo

Go to GitHub and create a **new public repository** (public is fine — your credentials are stored as encrypted secrets, never in the code).

### Step 2 — Add the files

Upload these to the root of your repo:
- `lms_scraper.py`
- An initial `cache.json` with this content:
  ```json
  {"announcements": [], "assignments": [], "quizzes": [], "files": [], "grades": [], "attendance": {}}
  ```

Create the folder `.github/workflows/` and upload `lms_notifier.yml` inside it.

```
your-repo/
├── .github/
│   └── workflows/
│       └── lms_notifier.yml
├── lms_scraper.py
└── cache.json
```

### Step 3 — Set up your Discord server

1. Create a new Discord server (or use an existing one)
2. Create **one text channel per subject** plus one for attendance:
   ```
   #career-essentials
   #computer-arch
   #creative-thinking
   #eda
   #env-sustainability
   #linear-algebra
   #microcontrollers
   #python
   #software-eng
   #tpcs
   #attendance
   ```

### Step 4 — Create Discord webhooks

For **each channel**, create a webhook:

1. Right-click the channel → **Edit Channel**
2. Go to **Integrations → Webhooks → New Webhook**
3. Name it (e.g. "LMS Notifier"), optionally set a profile picture
4. Click **Copy Webhook URL** — save it temporarily

You need **11 webhook URLs** total (10 subjects + 1 attendance).

### Step 5 — Add GitHub Secrets

Go to your repo: **Settings → Secrets and variables → Actions → New repository secret**

Add each of these:

| Secret Name | Value |
|---|---|
| `LMS_USERNAME` | Your LMS login username |
| `LMS_PASSWORD` | Your LMS login password |
| `WEBHOOK_CAREER_ESSENTIALS` | Webhook URL for #career-essentials |
| `WEBHOOK_COMPUTER_ARCH` | Webhook URL for #computer-arch |
| `WEBHOOK_CREATIVE_THINKING` | Webhook URL for #creative-thinking |
| `WEBHOOK_EDA` | Webhook URL for #eda |
| `WEBHOOK_ENV_SUSTAIN` | Webhook URL for #env-sustainability |
| `WEBHOOK_LINEAR_ALGEBRA` | Webhook URL for #linear-algebra |
| `WEBHOOK_MICROCONTROLLERS` | Webhook URL for #microcontrollers |
| `WEBHOOK_PYTHON` | Webhook URL for #python |
| `WEBHOOK_SOFTWARE_ENG` | Webhook URL for #software-eng |
| `WEBHOOK_TPCS` | Webhook URL for #tpcs |
| `WEBHOOK_ATTENDANCE` | Webhook URL for #attendance |

Your credentials are encrypted — GitHub never exposes them in logs or to anyone viewing the repo.

### Step 6 — Find your forum IDs (if needed)

The `SUBJECTS` list in `lms_scraper.py` already has the correct forum IDs for CSE II Sem at SIT-H. If they ever change:

1. Log into `https://lmssithyd.siu.edu.in`
2. Go to a subject's announcements forum
3. Look at the URL: `.../mod/forum/view.php?id=XXXX`
4. Update the matching `forum_id` in the script

### Step 7 — Test manually

1. Go to your repo → **Actions** tab
2. Click **📢 LMS Notifier** in the sidebar
3. Click **Run workflow** → set `fetch_attendance` to `true`, `lookback_days` to `7`
4. Click **Run workflow**

You should see everything flow into Discord within a minute or two.

### Step 8 — Done 🎉

The workflow runs **every hour automatically**. No further action needed.

> GitHub Actions schedules can be delayed 5–45 minutes on the free tier — this is normal.

---

## ⚙️ How it works

```
GitHub Actions (cron: every hour)
        │
        ▼
lms_scraper.py
        │
        ├─ login_to_lms()
        │     └─ POST credentials → warm-up dashboard + attendance cookies
        │
        ├─ discover_course_ids()
        │     └─ Reads dashboard to find course IDs for each subject
        │
        ├─ For each subject:
        │     ├─ fetch_announcements()  → send new posts to Discord
        │     ├─ fetch_assignments()    → send new assignments + deadline reminders
        │     ├─ fetch_quizzes()        → send new quizzes + deadline reminders
        │     ├─ fetch_files()          → send newly uploaded files
        │     └─ fetch_grades()         → send newly posted marks
        │
        ├─ fetch_attendance()
        │     └─ Only sends if percentages changed or you dropped below 75%
        │
        └─ save_cache()
              └─ Commits cache.json to repo so nothing ever gets re-sent
```

**Auto mode** (hourly cron): checks the last hour for announcements. Runs 7am–11pm IST only — no overnight noise.

**Manual mode** (workflow_dispatch): fetches announcements from the past N days. Attendance optional.

**Dedup cache:** every sent item (announcement, assignment, quiz, file, grade) is recorded in `cache.json` and committed back to the repo. Nothing ever gets re-posted between runs.

---

## 🔧 Adapting for other batches or colleges

**Different subjects:** Update the `SUBJECTS` list — change `name`, `code`, `forum_id`, and `webhook` for each. Add or remove entries freely.

**Different Moodle college:** Change `LMS_BASE` at the top of the script to your institution's URL. The login, scraping, and attendance logic works on any standard Moodle installation.

**Different attendance URL:** Log in manually, open your attendance page, copy the URL, and update `ATTENDANCE_URL` at the top of the script.

---

## 🐛 Troubleshooting

**Attendance shows "Fetch Failed"**
Check the GitHub Actions log for lines starting with `📋`. Most likely your attendance URL has changed — log in manually, open the attendance page, copy the URL, and update `ATTENDANCE_URL` in `lms_scraper.py`.

**No announcements showing up**
Verify your `forum_id` values. Open a subject's forum on LMS and check the URL for `?id=XXXX`. Also double-check the webhook URL for that channel.

**Assignments / quizzes / files not showing**
These require `course_id` to be discovered automatically from your dashboard. Check the Actions log for `discover_course_ids` — it should print a line like `✅ PY → course_id=1234` for each subject. If it finds nothing, your dashboard layout may differ — the feature will silently skip those subjects without breaking anything else.

**Login failed**
Double-check `LMS_USERNAME` and `LMS_PASSWORD` in GitHub Secrets. Try logging in manually to confirm.

**Cron not running**
GitHub pauses scheduled workflows after 60 days of no repo activity. The workflow includes a monthly keepalive commit to prevent this. If it still gets paused, open the Actions tab and re-enable it, or trigger one manual run.

---

## 📦 Dependencies

```
requests
beautifulsoup4
lxml
```

Installed automatically by the workflow. No local setup needed.

---

## 🔒 Security

- LMS credentials are stored as **GitHub Encrypted Secrets** — never visible in logs or to anyone viewing the repo
- The repo can be public — secrets are encrypted and separate from the code
- The script only sends credentials directly to your institution's Moodle login endpoint

---

## 🙌 Credits

Built by **Narala Lakshman Reddy** (CSE II Sem, SIT-H) out of frustration with missing announcements and surprise tests.

If this helped you — share it with your classmates. The more of us using it, the better. 😄

---

## 📄 License

MIT — free to use, modify, and share.
