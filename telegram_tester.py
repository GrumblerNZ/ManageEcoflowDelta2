#!/usr/bin/env python3
"""
EcoFlow Delta 2 - Telegram Test
"""

import json
import time
import requests
import random
import hashlib
import hmac
import paho.mqtt.client as mqtt
import ssl
from datetime import datetime
from zoneinfo import ZoneInfo

# ====================== CONFIG ======================
with open("config/secrets.json") as f:
    secrets = json.load(f)

DELTA2_SN = secrets["ecoFlow"]["delta2"]
ECOFLOW_API_URL = secrets["ecoFlow"]["api_url"]

TELEGRAM_TOKEN = secrets.get("telegram", {}).get("bot_token")
TELEGRAM_CHAT_ID = secrets.get("telegram", {}).get("chat_id")

# ====================== TELEGRAM ======================
def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Telegram token or chat_id not found in secrets.json")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        response = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML"
        }, timeout=10)
        if response.ok:
            print("✅ Message sent to Telegram!")
        else:
            print(f"❌ Telegram API error: {response.text}")
    except Exception as e:
        print(f"Telegram error: {e}")

def hmac_sha256(secret_key_string, message_string):
    secret_key_bytes = secret_key_string.encode('utf-8')
    message_bytes = message_string.encode('utf-8')
    hmac_obj = hmac.new(secret_key_bytes, message_bytes, hashlib.sha256)
    signature = hmac_obj.hexdigest()
    return signature

def get_ecoflow_headers(access_key, secret_key, prefix=""):
    nonce = random.randint(100000, 999999)
    timestamp = int(time.time() * 1000)
    string_to_sign = prefix + f"accessKey={access_key}&nonce={nonce}&timestamp={timestamp}"
    signature = hmac_sha256(secret_key, string_to_sign)
    
    headers = {
        "accessKey": access_key,
        "nonce": str(nonce),
        "timestamp": str(timestamp),
        "sign": signature,
        "content-type": "application/json"
    }
    return headers

# ====================== IMPROVED get_current_quota ======================
def get_current_quota():
    """Fetch quota using your ORIGINAL working signing method"""
    try:
        access_key = secrets["ecoFlow"]["access_key"]
        secret_key = secrets["ecoFlow"]["secret_key"]

        # Use the exact same function from your original working file
        headers = get_ecoflow_headers(access_key, secret_key)   # ← no prefix

        # Correct URL (matches your original code)
        url = f"{ECOFLOW_API_URL}{secrets['ecoFlow']['get_device_quota_url']}/all?sn={DELTA2_SN}"

        resp = requests.get(url, headers=headers, timeout=15)

        if not resp.ok:
            print(f"❌ Quota failed: {resp.status_code} - {resp.text}")
            return {}

        data = resp.json()
        if data.get("code") != "0":
            print(f"❌ API error: {data.get('message')}")
            return {}

        return data.get("data", {})

    except Exception as e:
        print(f"❌ Exception: {e}")
        return {}


# ====================== MAIN ======================
if __name__ == "__main__":
    print("🚀 Telegram Tester started")
    
    nz_time = datetime.now(ZoneInfo("Pacific/Auckland")).strftime('%Y-%m-%d %H:%M:%S %Z')
    quota = get_current_quota()
    #print(f"Current quota data: {quota}")
    soc = quota.get("pd.soc", 50)
    chargeWatt = quota.get("mppt.cfgChgWatts", 200)
    
    send_telegram(
        f"🚀 <b>Telegram Test Successful!</b>\n\n"
        f"Time in NZ: {nz_time}\n"
        f"Your Delta 2 controller is ready to go!"
        f"\n\nCurrent SOC: {soc}%"
        f"\nCurrent Charge Watts: {chargeWatt}W"
    )