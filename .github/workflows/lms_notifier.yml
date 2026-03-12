name: 📢 LMS Notifier

on:
  # ── Manual trigger ───────────────────────────────────────────────
  workflow_dispatch:
    inputs:
      mode:
        description: "Mode: 'today' = all of today's posts + attendance | 'manual' = custom N-day lookback"
        required: false
        default: "today"
        type: choice
        options:
          - today
          - manual
      fetch_attendance:
        description: "[manual only] Fetch attendance? (true/false)"
        required: false
        default: "true"
      lookback_days:
        description: "[manual only] Days to look back"
        required: false
        default: "7"

  # ── Hourly auto trigger ──────────────────────────────────────────
  # GitHub Actions cron uses UTC. IST = UTC+5:30
  # "0 * * * *" fires every hour on the hour (UTC)
  # e.g. 8 PM IST = 14:30 UTC, 9 PM IST = 15:30 UTC
  # NOTE: GitHub may delay scheduled runs by up to ~15 mins under load.
  # NOTE: GitHub PAUSES scheduled workflows on repos with no activity
  #       for 60 days. The keepalive job below prevents this.
  schedule:
    - cron: "0 * * * *"   # every hour on the hour (UTC)

jobs:
  # ── Keepalive — prevents GitHub from pausing the schedule ────────
  # Runs once a month and makes a dummy commit to keep the repo active.
  keepalive:
    name: 🔄 Keepalive (prevent schedule pause)
    runs-on: ubuntu-latest
    if: github.event_name == 'schedule'
    steps:
      - name: 📥 Checkout
        uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}

      - name: 🔄 Keepalive ping
        run: |
          # Only commit once per month (day 1 of month, hour 0 UTC)
          DAY=$(date -u +%d)
          HOUR=$(date -u +%H)
          if [ "$DAY" = "01" ] && [ "$HOUR" = "00" ]; then
            git config user.name  "github-actions[bot]"
            git config user.email "github-actions[bot]@users.noreply.github.com"
            echo "$(date -u)" > .keepalive
            git add .keepalive
            git commit -m "chore: keepalive [skip ci]" || echo "Nothing to commit"
            git push
          else
            echo "Not keepalive day — skipping commit."
          fi

  # ── Main notifier job ─────────────────────────────────────────────
  notify:
    name: Fetch & Send LMS Updates
    runs-on: ubuntu-latest

    steps:
      # 1. Checkout
      - name: 📥 Checkout repo
        uses: actions/checkout@v4

      # 2. Python
      - name: 🐍 Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      # 3. Dependencies
      - name: 📦 Install dependencies
        run: pip install requests beautifulsoup4 lxml

      # 4. Set run mode
      - name: ⚙️ Set run mode
        id: mode
        run: |
          if [ "${{ github.event_name }}" = "workflow_dispatch" ]; then
            MODE="${{ github.event.inputs.mode }}"
            echo "RUN_MODE=${MODE}" >> $GITHUB_ENV
            if [ "${MODE}" = "today" ]; then
              echo "FETCH_ATTENDANCE=true" >> $GITHUB_ENV
              echo "LOOKBACK_DAYS=0"       >> $GITHUB_ENV
            else
              echo "FETCH_ATTENDANCE=${{ github.event.inputs.fetch_attendance }}" >> $GITHUB_ENV
              echo "LOOKBACK_DAYS=${{ github.event.inputs.lookback_days }}"       >> $GITHUB_ENV
            fi
          else
            # Cron: auto mode, 1-hour window
            echo "RUN_MODE=auto"           >> $GITHUB_ENV
            echo "FETCH_ATTENDANCE=true"   >> $GITHUB_ENV
            echo "LOOKBACK_DAYS=0"         >> $GITHUB_ENV
          fi

      # 5. Run notifier
      - name: 🚀 Run LMS Notifier
        env:
          LMS_USERNAME: ${{ secrets.LMS_USERNAME }}
          LMS_PASSWORD: ${{ secrets.LMS_PASSWORD }}

          WEBHOOK_CAREER_ESSENTIALS: ${{ secrets.WEBHOOK_CAREER_ESSENTIALS }}
          WEBHOOK_COMPUTER_ARCH:     ${{ secrets.WEBHOOK_COMPUTER_ARCH }}
          WEBHOOK_CREATIVE_THINKING: ${{ secrets.WEBHOOK_CREATIVE_THINKING }}
          WEBHOOK_EDA:               ${{ secrets.WEBHOOK_EDA }}
          WEBHOOK_ENV_SUSTAIN:       ${{ secrets.WEBHOOK_ENV_SUSTAIN }}
          WEBHOOK_LINEAR_ALGEBRA:    ${{ secrets.WEBHOOK_LINEAR_ALGEBRA }}
          WEBHOOK_MICROCONTROLLERS:  ${{ secrets.WEBHOOK_MICROCONTROLLERS }}
          WEBHOOK_PYTHON:            ${{ secrets.WEBHOOK_PYTHON }}
          WEBHOOK_SOFTWARE_ENG:      ${{ secrets.WEBHOOK_SOFTWARE_ENG }}
          WEBHOOK_TPCS:              ${{ secrets.WEBHOOK_TPCS }}

          WEBHOOK_ATTENDANCE: ${{ secrets.WEBHOOK_ATTENDANCE }}

        run: python lms_scraper.py
