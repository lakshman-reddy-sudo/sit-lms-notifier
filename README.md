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
- Logs into your LMS every 2 hours automatically
- Scrapes new announcements from all 10 subjects
- Pulls your consolidated attendance report
- Sends everything to a **Discord server** with rich, formatted messages

Zero manual effort after setup. It just runs.

---

## ✨ What you get in Discord

### 📬 Announcements (per subject channel)
Every new announcement shows up with:
- The full announcement body (not a summary — the whole thing)
- Who posted it and when
- A direct link to the LMS post
- A friendly "all clear" message when there's nothing new that hour

### 📋 Attendance Report (every hour)
A clean summary showing:
- Attended / Total sessions for every subject
- Your percentage, color-coded 🔴🟡🟢
- **How many more classes you need to hit 75%** (if you're below)
- **How many classes you can safely skip** (if you're above)
- Overall totals across all subjects

---

## 🗂️ Project Structure

```
sit-lms-notifier/
├── lms_scraper.py          # Main script — login, scrape, send to Discord
├── lms_notifier.yml        # GitHub Actions workflow (place in .github/workflows/)
├── requirements.txt        # Python dependencies
├── cache.json              # Tracks already-sent announcements (auto-managed)
└── README.md               # This file
```

---

## 🚀 Setup Guide (step by step)

This might look like a lot, but it's a one-time setup that takes about **15–20 minutes**. Follow each step carefully and you'll be done.

### Step 1 — Fork or create the repo

Go to GitHub and create a **new private repository**. Private is important — your LMS credentials will be stored here as secrets, so never make it public.

Clone it to your machine or just work directly on GitHub.

### Step 2 — Add the files

Upload these files to the root of your repo:
- `lms_scraper.py`
- `requirements.txt`
- `cache.json` (initial content: `[]`)

Then create the folder `.github/workflows/` and upload `lms_notifier.yml` inside it.

Your repo structure should look like:
```
your-repo/
├── .github/
│   └── workflows/
│       └── lms_notifier.yml
├── lms_scraper.py
├── requirements.txt
└── cache.json
```

### Step 3 — Create your Discord server

1. Open Discord and click the **+** button on the left sidebar to create a new server
2. Name it something like **"LMS Notifier"**
3. Create **one text channel per subject** plus one for attendance. You can name them whatever you like, e.g.:
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

For **each channel** (including attendance), you need to create a webhook:

1. Right-click the channel → **Edit Channel**
2. Go to **Integrations** → **Webhooks** → **New Webhook**
3. Give it a name (e.g. "LMS Notifier") and optionally upload a bot profile picture
4. Click **Copy Webhook URL** and save it somewhere temporarily

You'll need **11 webhook URLs** total (10 subjects + 1 attendance).

### Step 5 — Customise `lms_scraper.py` for your subjects

Open `lms_scraper.py` and find the `SUBJECTS` list near the top. Each subject has a `forum_id` — this is the ID from your LMS forum URL.

To find your forum IDs:
1. Log into your LMS at `https://lmssithyd.siu.edu.in`
2. Go to a subject's announcements forum
3. Look at the URL — it will look like `.../mod/forum/view.php?id=XXXX`
4. The number after `id=` is your `forum_id`

Update each subject's `forum_id` to match your own. The subject names, emojis, and colors can also be customised to your liking.

### Step 6 — Add GitHub Secrets

This is where you store sensitive info safely. Go to your GitHub repo:

**Settings → Secrets and variables → Actions → New repository secret**

Add each of these secrets one by one:

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

**Important:** Your credentials are stored as encrypted secrets — GitHub never exposes them in logs or to anyone else.

### Step 7 — Test it manually

Before waiting for the hourly schedule, trigger a manual run to make sure everything works:

1. Go to your repo on GitHub
2. Click **Actions** tab
3. Click **📢 LMS Notifier** in the left sidebar
4. Click **Run workflow** (top right)
5. Set `fetch_attendance` to `true` and `lookback_days` to `7`
6. Click **Run workflow**

Watch the logs in real time. You should see announcements and attendance flowing into your Discord within a minute or two.

### Step 8 — You're done! 🎉

The workflow runs **automatically every 2 hours**. You don't need to do anything else. Sit back and let it work.

> **Note:** GitHub Actions schedules are not perfectly on the hour — they can be delayed by 5–45 minutes depending on GitHub's server load. This is normal and expected on the free tier.

---

## ⚙️ How it works (technical overview)

```
GitHub Actions (cron: every 2 hours)
        │
        ▼
lms_scraper.py
        │
        ├─ login_to_lms()
        │     └─ POST credentials to Moodle login endpoint
        │        Warm-up: visits dashboard + attendance URL to seed session cookies
        │
        ├─ fetch_announcements()  [for each subject]
        │     ├─ GET forum page → scrape discussion rows
        │     ├─ For each new post → GET discuss.php → extract full body + author + date
        │     └─ send_announcements_to_discord() → POST to webhook
        │
        └─ fetch_attendance()
              ├─ GET attendance report page
              ├─ Parse table → subject / total / attended / percentage
              └─ send_attendance_to_discord() → POST to webhook
```

**Auto mode** (2 hourly cron): only sends announcements posted in the last 2 hours. Sends an "all clear" message per subject if nothing is new. Always sends attendance.

**Manual mode** (workflow_dispatch): fetches announcements from the past N days (default 7). Attendance is optional via the `fetch_attendance` input.

---

## 🔧 Adapting for other SIU batches / colleges

The script is built specifically for SIT-H. To adapt it:

**Different subjects:** Update the `SUBJECTS` list in `lms_scraper.py` — change the `name`, `code`, `forum_id`, and `webhook` for each subject. Add or remove entries as needed.

**Different college on Moodle:** Change `LMS_BASE` at the top of the script to your institution's Moodle URL. The login, forum scraping, and attendance parsing logic should work on any standard Moodle installation.

**Different attendance URL:** Find your attendance page URL by logging in manually, navigating to your attendance report, and copying the URL from the browser. Update `ATTENDANCE_URL` at the top of the script.

---

## 🐛 Troubleshooting

**Attendance shows "Fetch Failed"**
Go to GitHub Actions → open the latest run log → look for lines starting with `📋`. This will tell you what the parser found. Most likely your attendance URL is different — log in manually, open your attendance page, copy the URL, and update `ATTENDANCE_URL` in `lms_scraper.py`.

**No announcements showing up**
Check that your `forum_id` values are correct. Log into LMS, open a subject's forum, check the URL for `?id=XXXX`. Also make sure the webhooks are correct and the channels exist.

**Login failed**
Double-check `LMS_USERNAME` and `LMS_PASSWORD` in your GitHub secrets. Try logging into the LMS manually to confirm they work.

**Cron not running**
GitHub pauses scheduled workflows on repositories with no activity for 60 days. The workflow includes a monthly keepalive commit to prevent this. If it still gets paused, just open the Actions tab and re-enable it, or trigger a manual run.

**"Settings Star this discussion" appearing in body**
This is old cached output — update to the latest `lms_scraper.py` which strips this Moodle UI noise automatically.

---

## 📦 Dependencies

```
requests
beautifulsoup4
lxml
```

Installed automatically by the GitHub Actions workflow. No local installation needed.

---

## 🔒 Security

- Your LMS credentials are stored as **GitHub Encrypted Secrets** — they are never visible in logs, to collaborators, or to GitHub itself
- The script never stores or transmits your credentials anywhere other than directly to your institution's LMS login endpoint

---

## 🙌 Credits

Built by **Narala Lakshman Reddy** (CSE, SIT-H) out of frustration with missing announcements.

If this helped you never miss a test or drop below 75% attendance — share it with your classmates. The more of us who use it, the happier we all are. 😄

---

## 📄 License

MIT License — free to use, modify, and share.
