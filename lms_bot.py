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

    requests.post(DISCORD_WEBHOOK, json={"content": msg})


def load_cache():

    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except:
        return []


def save_cache(data):

    with open(CACHE_FILE, "w") as f:
        json.dump(data, f)


def login():

    r = session.get(f"{LMS_URL}/login/index.php")

    token = r.text.split('name="logintoken" value="')[1].split('"')[0]

    payload = {
        "username": USERNAME,
        "password": PASSWORD,
        "logintoken": token
    }

    session.post(f"{LMS_URL}/login/index.php", data=payload)


def get_sesskey():

    page = session.get(f"{LMS_URL}/my/")

    text = page.text

    sesskey = text.split('"sesskey":"')[1].split('"')[0]

    return sesskey


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

    return r.json()


def clean_html(html):

    import re

    clean = re.sub('<.*?>', '', html)

    return clean.strip()


def main():

    login()

    sesskey = get_sesskey()

    cache = load_cache()

    events = fetch_timeline(sesskey)

    for item in events:

        if "data" not in item:
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

            attachments = "None"

            if "pluginfile.php" in description:
                attachments = "Present"

            discord_msg = f"""
📢 **LMS UPDATE**

Course: {course}

Title: {name}

Message:
{message}

Attachments: {attachments}
"""

            send_discord(discord_msg)

            cache.append(key)

    save_cache(cache)


if __name__ == "__main__":

    main()def login():

    r = session.get(f"{LMS_URL}/login/index.php")

    token = r.text.split('name="logintoken" value="')[1].split('"')[0]

    payload = {
        "username": USERNAME,
        "password": PASSWORD,
        "logintoken": token
    }

    session.post(f"{LMS_URL}/login/index.php", data=payload)


def fetch_timeline():

    url = f"{LMS_URL}/lib/ajax/service.php?sesskey="

    payload = [{
        "index": 0,
        "methodname": "core_calendar_get_action_events_by_timesort",
        "args": {
            "limitnum": 10
        }
    }]

    r = session.post(url, json=payload)

    return r.json()


def main():

    login()

    cache = load_cache()

    events = fetch_timeline()

    for item in events:

        data = item["data"]["events"]

        for e in data:

            name = e["name"]

            description = e.get("description", "")

            course = e.get("course", {}).get("fullname", "")

            key = name + course

            if key not in cache:

                msg = f"""
📢 **LMS Update**

Course: {course}

Title: {name}

Message:
{description}
"""

                send_discord(msg)

                cache.append(key)

    save_cache(cache)


if __name__ == "__main__":
    main()
