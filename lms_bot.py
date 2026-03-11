import re
import requests
import json
import os

LMS_URL = "https://lmssithyd.siu.edu.in"

USERNAME = os.getenv("LMS_USER")
PASSWORD = os.getenv("LMS_PASS")
DISCORD_WEBHOOK = os.getenv("WEBHOOK_URL")

CACHE_FILE = "cache.json"

session = requests.Session()


def send_discord(msg):
    resp = requests.post(DISCORD_WEBHOOK, json={"content": msg})
    if not resp.ok:
        print(f"[WARN] Discord webhook failed: {resp.status_code} {resp.text}")


def load_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def save_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def login():
    r = session.get(f"{LMS_URL}/login/index.php")
    if 'name="logintoken" value="' not in r.text:
        raise RuntimeError("Could not find login token — LMS page structure may have changed.")
    token = r.text.split('name="logintoken" value="')[1].split('"')[0]
    payload = {
        "username": USERNAME,
        "password": PASSWORD,
        "logintoken": token,
    }
    resp = session.post(f"{LMS_URL}/login/index.php", data=payload)
    if "loginerrors" in resp.text or "Invalid login" in resp.text:
        raise RuntimeError("Login failed — check LMS_USER and LMS_PASS secrets.")


def get_sesskey():
    page = session.get(f"{LMS_URL}/my/")
    if '"sesskey":"' not in page.text:
        raise RuntimeError("Could not get sesskey — login may have failed silently.")
    return page.text.split('"sesskey":"')[1].split('"')[0]


def fetch_timeline(sesskey):
    url = f"{LMS_URL}/lib/ajax/service.php?sesskey={sesskey}"
    payload = [{
        "index": 0,
        "methodname": "core_calendar_get_action_events_by_timesort",
        "args": {
            "limitnum": 10
        }
    }]
    r = session.post(url, json=payload)
    r.raise_for_status()
    return r.json()


def clean_html(html):
    clean = re.sub(r'<.*?>', '', html)
    return clean.strip()


def main():
    if not USERNAME or not PASSWORD or not DISCORD_WEBHOOK:
        raise RuntimeError("One or more required env vars are missing: LMS_USER, LMS_PASS, WEBHOOK_URL")

    login()
    sesskey = get_sesskey()
    cache = load_cache()
    events = fetch_timeline(sesskey)

    new_events = 0
    for item in events:
        if "data" not in item:
            print(f"[WARN] Skipping item with no 'data' key: {item}")
            continue

        event_list = item["data"].get("events", [])

        for e in event_list:
            name = e.get("name", "")
            description = e.get("description", "")
            course = e.get("course", {}).get("fullname", "")
            key = name + course

            if key in cache:
                continue

            message = clean_html(description)
            attachments = "Present" if "pluginfile.php" in description else "None"

            discord_msg = (
                f"📢 **LMS UPDATE**\n\n"
                f"**Course:** {course}\n"
                f"**Title:** {name}\n\n"
                f"**Message:**\n{message}\n\n"
                f"**Attachments:** {attachments}"
            )

            send_discord(discord_msg)
            cache.append(key)
            new_events += 1

    save_cache(cache)
    print(f"Done. {new_events} new event(s) sent to Discord.")


if __name__ == "__main__":
    main()
