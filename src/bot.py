import time
import json
import asyncio
from time import perf_counter
import os
import subprocess

from decouple import AutoConfig
import requests

from scraper import run_search

def install_browsers():
    try:
        subprocess.run(["playwright", "install", "chromium"], check=True)
        subprocess.run(["playwright", "install-deps", "chromium"], check=True)
        print("Playwright browsers installed successfully")
    except subprocess.CalledProcessError as e:
        print(f"Failed to install browsers: {e}")

# Run browser installation
install_browsers()


config = AutoConfig()
BOT_API_KEY = config("BOT_API_KEY")

# File to store user chat IDs
LAST_AVAILABLE_TIME = perf_counter()
HOUR_DIFFERENCE = 12

USERS_FILE = "users.json"
WAIT_SECOND = 60
INFO_SENT = False


def load_users():
    """Load user chat IDs from file"""
    try:
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def save_users(users):
    """Save user chat IDs to file"""
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f)


def get_updates(offset=0):
    """Get updates from Telegram API"""
    url = f"https://api.telegram.org/bot{BOT_API_KEY}/getUpdates"
    params = {"offset": offset}

    try:
        response = requests.get(url, params=params)
        return response.json() if response.status_code == 200 else None
    except Exception as e:
        print(f"Error getting updates: {e}")
        return None


def update_users():
    """Update the list of registered users"""
    users = load_users()
    last_update_id = 0

    updates = get_updates()
    if not updates or not updates.get('ok'):
        return users

    for update in updates.get('result', []):
        if 'message' in update:
            chat_id = update['message']['chat']['id']
            if chat_id not in users:
                users.append(chat_id)
                print(f"New user registered: {chat_id}")

        last_update_id = max(last_update_id, update['update_id'])

    # Clear processed updates
    if last_update_id > 0:
        get_updates(last_update_id + 1)

    save_users(users)
    return users


def send_message(chat_id, text):
    """Send a message to a specific chat"""
    url = f"https://api.telegram.org/bot{BOT_API_KEY}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": text
    }

    try:
        response = requests.post(url, data=data)
        if response.status_code == 200:
            print(f"Message sent to {chat_id}")
        else:
            print(f"Failed to send message to {chat_id}: {response.text}")
    except Exception as e:
        print(f"Error sending message: {e}")


async def main():
    global LAST_AVAILABLE_TIME, INFO_SENT
    """Main loop to send hello messages every 5 seconds"""
    print("Bot started! Sending trp notification to all "
          "registered users when found...")

    while True:
        # Update user list with any new users
        users = update_users()

        if not users:
            print("No registered users yet. Users need to send a message to the bot first.")
        elif not INFO_SENT:
            print(f"Sending to {len(users)} registered users...")
            for chat_id in users:
                send_message(chat_id, "Bot started working...")

        final_results = await run_search()
        curr_time = perf_counter()

        if final_results:
            for chat_id in users:
                send_message(chat_id, str(final_results))

        elif curr_time - LAST_AVAILABLE_TIME > HOUR_DIFFERENCE * 3600:
            for chat_id in users:
                    send_message(chat_id,
                        "No available days found in the last "
                          f"{HOUR_DIFFERENCE} hours")

        await asyncio.sleep(WAIT_SECOND)
        print(f"Sleeping for {WAIT_SECOND} seconds...")
        INFO_SENT = True


if __name__ == "__main__":
    asyncio.run(main())