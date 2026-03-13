# 📢 SIT-LMS Notifier

> **Never miss a test, assignment, or announcement again.**
> Get every LMS update + your attendance report delivered straight to Discord — automatically, every hour.

---

## 🤔 What is this?

If you're a student at **Symbiosis Institute of Technology, Hyderabad**, you know the pain:
- You forget to check the LMS and miss an important announcement about a test
- You lose track of your attendance and suddenly realise you're below 75%
- You check each subject one by one — 10 subjects, 10 clicks, every single day

This project fixes all of that. It's a **GitHub Actions bot** that:
- Logs into your LMS every hour automatically
- Scrapes new announcements from all 10 subjects
- Pulls your consolidated attendance report
- Sends everything to a **Discord server** with rich, formatted messages

Zero manual effort after setup. It just runs.

---

## ✨ What you get in Discord

### 📬 Announcements (per subject bot)
Each subject has its own Discord bot. New announcements show up with:
- The post title as a clickable link directly to the LMS
- Who posted it and when
- A preview of the announcement body
- In auto mode: **silent** when nothing new (no spam)
- In manual mode: a friendly "all clear" message when there's nothing new

### 📋 Attendance Report (attendance bot)
A clean embed showing every subject with:
- Attended / Total sessions
- Percentage, color-coded 🔴 🟡 🟢
- **How many more classes you need to hit 75%** (if you're below)
- **How many classes you can safely skip** (if you're above)
- Overall totals across all subjects

---

## 🗂️ Project Structure

```
sit-lms-notifier/
├── lms_scraper.py               # Main script — login, scrape, send to Discord
├── .github/
│   └── workflows/
│       └── lms_notifier.yml     # GitHub Actions workflow
├── requirements.txt             # Python dependencies
└── README.md                    # This file
```

---

## ⚙️ Run Modes

The bot has three modes:

| Mode | Triggered by | What it does |
|---|---|---|
| **`auto`** | Hourly cron (automatic) | Checks last **1 hour** only — alerts you the moment something new is posted. Silent if nothing new. Always sends attendance. |
| **`today`** | Manual → Run workflow | Fetches everything posted **since midnight IST** — your full day summary on demand. Always sends attendance. |
| **`manual`** | Manual → Run workflow | Fetches last N days of announcements. Attendance optional. |

**The idea:** the hourly auto runs are a live alert system — you get pinged only when a teacher actually posts something new. Use **`today`** manually any time you want a full catch-up of everything from today.

---

## 🚀 Setup Guide

This is a one-time setup that takes about **15–20 minutes**.

### Step 1 — Create the repo

Go to GitHub and create a **new private repository**. Private is important — your LMS credentials will be stored here as secrets.

### Step 2 — Add the files

Upload these files to the root of your repo:
- `lms_scraper.py`
- `requirements.txt`

Create the folder `.github/workflows/` and upload `lms_notifier.yml` inside it.

```
your-repo/
├── .github/
│   └── workflows/
│       └── lms_notifier.yml
├── lms_scraper.py
└── requirements.txt
```

### Step 3 — Create your Discord server

1. Open Discord → click **+** on the sidebar → create a new server (e.g. "LMS Notifier")
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

For **each channel** (11 total — 10 subjects + attendance):

1. Right-click the channel → **Edit Channel**
2. Go to **Integrations** → **Webhooks** → **New Webhook**
3. Give it a name and click **Copy Webhook URL**

### Step 5 — Add GitHub Secrets

Go to your repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret Name | Value |
|---|---|
| `LMS_USERNAME` | Your LMS login username |
| `LMS_PASSWORD` | Your LMS login password |
| `WEBHOOK_CAREER_ESSENTIALS` | Webhook URL for that channel |
| `WEBHOOK_COMPUTER_ARCH` | Webhook URL for that channel |
| `WEBHOOK_CREATIVE_THINKING` | Webhook URL for that channel |
| `WEBHOOK_EDA` | Webhook URL for that channel |
| `WEBHOOK_ENV_SUSTAIN` | Webhook URL for that channel |
| `WEBHOOK_LINEAR_ALGEBRA` | Webhook URL for that channel |
| `WEBHOOK_MICROCONTROLLERS` | Webhook URL for that channel |
| `WEBHOOK_PYTHON` | Webhook URL for that channel |
| `WEBHOOK_SOFTWARE_ENG` | Webhook URL for that channel |
| `WEBHOOK_TPCS` | Webhook URL for that channel |
| `WEBHOOK_ATTENDANCE` | Webhook URL for the attendance channel |

Your credentials are stored as encrypted secrets — GitHub never exposes them in logs.

### Step 6 — Test it manually

1. Go to your repo → **Actions** tab
2. Click **📢 LMS Notifier** in the sidebar
3. Click **Run workflow**
4. Select mode: **`today`**, click **Run workflow**

You should see announcements and attendance flowing into Discord within a minute or two.

### Step 7 — You're done! 🎉

The workflow runs **automatically every hour**. You don't need to do anything else.

> **Note:** GitHub Actions cron can be delayed by 5–45 minutes under load on the free tier. This is normal.

---

## 🔧 How it works (technical overview)

```
GitHub Actions
    │
    ├─ Cron (every hour) ──────────────── RUN_MODE = auto
    │                                     LOOKBACK = last 1 hour
    │
    └─ Manual trigger
          ├─ mode = today ──────────────── RUN_MODE = today
          │                                LOOKBACK = since midnight IST
          └─ mode = manual ─────────────── RUN_MODE = manual
                                           LOOKBACK = last N days

lms_scraper.py
    │
    ├─ login_to_lms()          [Session 1 — for announcements]
    │     └─ POST credentials to Moodle login
    │
    ├─ fetch_announcements()   [for each of 10 subjects]
    │     ├─ GET forum/view.php → scrape discuss.php links
    │     ├─ GET each discuss.php → extract title + body preview + author + date
    │     └─ send_announcements_to_discord() → POST to subject webhook
    │
    └─ login_to_lms()          [Session 2 — fresh login for attendance]
          └─ fetch_attendance()
                ├─ GET attendance-report page → parse table
                └─ send_attendance_to_discord() → POST to attendance webhook
```

Two separate login sessions are used — one for announcements, one for attendance — to avoid session expiry after many page fetches.

---

## 🔧 Adapting for other SIU batches / colleges

**Different subjects:** Update the `SUBJECTS` list in `lms_scraper.py` — change `name`, `code`, `forum_id`, and `webhook` for each subject.

**Different college on Moodle:** Change `LMS_BASE` at the top of the script to your institution's Moodle URL.

**Different attendance URL:** Log in manually, navigate to your attendance report, copy the URL from the browser, and update `ATTENDANCE_URL` in `lms_scraper.py`.

---

## 🐛 Troubleshooting

**Attendance shows "Fetch Failed"**
Open the Actions run log and look for lines starting with `📋`. Update `ATTENDANCE_URL` in `lms_scraper.py` to match your actual attendance page URL.

**No announcements showing up**
Check `forum_id` values — log into LMS, open a subject's forum, check the URL for `?id=XXXX`. Also verify webhook URLs are correct.

**Login failed**
Double-check `LMS_USERNAME` and `LMS_PASSWORD` in GitHub Secrets. Try logging in manually to confirm they work.

**Cron stopped running**
GitHub pauses scheduled workflows after 60 days of repo inactivity. The workflow includes a monthly keepalive commit to prevent this. If it still pauses, open the Actions tab and re-enable it, or trigger a manual run.

---

## 📦 Dependencies

```
requests
beautifulsoup4
lxml
```

Installed automatically by GitHub Actions. No local setup needed.

---

## 🔒 Security

- LMS credentials are stored as **GitHub Encrypted Secrets** — never visible in logs or to anyone else
- The script only sends credentials directly to your institution's LMS login endpoint

---

## 🙌 Credits

Built by **Narala Lakshman Reddy** (CSE, SIT-H) out of frustration with missing announcements.

If this helped you never miss a test or drop below 75% — share it with your classmates. 😄

---

## 📄 License

MIT License — free to use, modify, and share.|---|---|---|
| **`auto`** | Hourly cron (automatic) | Checks last **1 hour** only — alerts you the moment something new is posted. Silent if nothing new. Always sends attendance. |
| **`today`** | Manual → Run workflow | Fetches everything posted **since midnight IST** — your full day summary on demand. Always sends attendance. |
| **`manual`** | Manual → Run workflow | Fetches last N days of announcements. Attendance optional. |

**The idea:** the hourly auto runs are a live alert system — you get pinged only when a teacher actually posts something new. Use **`today`** manually any time you want a full catch-up of everything from today.

---

## 🚀 Setup Guide

This is a one-time setup that takes about **15–20 minutes**.

### Step 1 — Create the repo

Go to GitHub and create a **new private repository**. Private is important — your LMS credentials will be stored here as secrets.

### Step 2 — Add the files

Upload these files to the root of your repo:
- `lms_scraper.py`
- `requirements.txt`

Create the folder `.github/workflows/` and upload `lms_notifier.yml` inside it.

```
your-repo/
├── .github/
│   └── workflows/
│       └── lms_notifier.yml
├── lms_scraper.py
└── requirements.txt
```

### Step 3 — Create your Discord server

1. Open Discord → click **+** on the sidebar → create a new server (e.g. "LMS Notifier")
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

For **each channel** (11 total — 10 subjects + attendance):

1. Right-click the channel → **Edit Channel**
2. Go to **Integrations** → **Webhooks** → **New Webhook**
3. Give it a name and click **Copy Webhook URL**

### Step 5 — Add GitHub Secrets

Go to your repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret Name | Value |
|---|---|
| `LMS_USERNAME` | Your LMS login username |
| `LMS_PASSWORD` | Your LMS login password |
| `WEBHOOK_CAREER_ESSENTIALS` | Webhook URL for that channel |
| `WEBHOOK_COMPUTER_ARCH` | Webhook URL for that channel |
| `WEBHOOK_CREATIVE_THINKING` | Webhook URL for that channel |
| `WEBHOOK_EDA` | Webhook URL for that channel |
| `WEBHOOK_ENV_SUSTAIN` | Webhook URL for that channel |
| `WEBHOOK_LINEAR_ALGEBRA` | Webhook URL for that channel |
| `WEBHOOK_MICROCONTROLLERS` | Webhook URL for that channel |
| `WEBHOOK_PYTHON` | Webhook URL for that channel |
| `WEBHOOK_SOFTWARE_ENG` | Webhook URL for that channel |
| `WEBHOOK_TPCS` | Webhook URL for that channel |
| `WEBHOOK_ATTENDANCE` | Webhook URL for the attendance channel |

Your credentials are stored as encrypted secrets — GitHub never exposes them in logs.

### Step 6 — Test it manually

1. Go to your repo → **Actions** tab
2. Click **📢 LMS Notifier** in the sidebar
3. Click **Run workflow**
4. Select mode: **`today`**, click **Run workflow**

You should see announcements and attendance flowing into Discord within a minute or two.

### Step 7 — You're done! 🎉

The workflow runs **automatically every hour**. You don't need to do anything else.

> **Note:** GitHub Actions cron can be delayed by 5–45 minutes under load on the free tier. This is normal.

---

## 🔧 How it works (technical overview)

```
GitHub Actions
    │
    ├─ Cron (every hour) ──────────────── RUN_MODE = auto
    │                                     LOOKBACK = last 1 hour
    │
    └─ Manual trigger
          ├─ mode = today ──────────────── RUN_MODE = today
          │                                LOOKBACK = since midnight IST
          └─ mode = manual ─────────────── RUN_MODE = manual
                                           LOOKBACK = last N days

lms_scraper.py
    │
    ├─ login_to_lms()          [Session 1 — for announcements]
    │     └─ POST credentials to Moodle login
    │
    ├─ fetch_announcements()   [for each of 10 subjects]
    │     ├─ GET forum/view.php → scrape discuss.php links
    │     ├─ GET each discuss.php → extract title + body preview + author + date
    │     └─ send_announcements_to_discord() → POST to subject webhook
    │
    └─ login_to_lms()          [Session 2 — fresh login for attendance]
          └─ fetch_attendance()
                ├─ GET attendance-report page → parse table
                └─ send_attendance_to_discord() → POST to attendance webhook
```

Two separate login sessions are used — one for announcements, one for attendance — to avoid session expiry after many page fetches.

---

## 🔧 Adapting for other SIU batches / colleges

**Different subjects:** Update the `SUBJECTS` list in `lms_scraper.py` — change `name`, `code`, `forum_id`, and `webhook` for each subject.

**Different college on Moodle:** Change `LMS_BASE` at the top of the script to your institution's Moodle URL.

**Different attendance URL:** Log in manually, navigate to your attendance report, copy the URL from the browser, and update `ATTENDANCE_URL` in `lms_scraper.py`.

---

## 🐛 Troubleshooting

**Attendance shows "Fetch Failed"**
Open the Actions run log and look for lines starting with `📋`. Update `ATTENDANCE_URL` in `lms_scraper.py` to match your actual attendance page URL.

**No announcements showing up**
Check `forum_id` values — log into LMS, open a subject's forum, check the URL for `?id=XXXX`. Also verify webhook URLs are correct.

**Login failed**
Double-check `LMS_USERNAME` and `LMS_PASSWORD` in GitHub Secrets. Try logging in manually to confirm they work.

**Cron stopped running**
GitHub pauses scheduled workflows after 60 days of repo inactivity. The workflow includes a monthly keepalive commit to prevent this. If it still pauses, open the Actions tab and re-enable it, or trigger a manual run.

---

## 📦 Dependencies

```
requests
beautifulsoup4
lxml
```

Installed automatically by GitHub Actions. No local setup needed.

---

## 🔒 Security

- LMS credentials are stored as **GitHub Encrypted Secrets** — never visible in logs or to anyone else
- The script only sends credentials directly to your institution's LMS login endpoint

---

## 🙌 Credits

Built by **Narala Lakshman Reddy** (CSE, SIT-H) out of frustration with missing announcements.

If this helped you never miss a test or drop below 75% — share it with your classmates. 😄

---

## 📄 License

MIT License — free to use, modify, and share.
