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
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

def get_excess_solar_watts():
    username = secrets["username"]
    password = secrets["password"]

    envoy_serial = secrets["serial_number"]
    data = {'user[email]': username, 'user[password]': password}
    response = requests.post('http://enlighten.enphaseenergy.com/login/login.json?', data=data)
    response_data = json.loads(response.text)

    data = {'session_id': response_data['session_id'], 'serial_num': envoy_serial, 'username': username}
    response = requests.post('http://entrez.enphaseenergy.com/tokens', json=data)
    token_raw = response.text

    ENPHASE_API_URL = secrets["api_url"]
    headers = {"Authorization": f"Bearer {token_raw}", "Accept": "application/json"}
    
    # Envoy uses self-signed cert — this is expected and safe for local LAN access
    response = requests.get(ENPHASE_API_URL, headers=headers, verify=False, timeout=30)
    response.raise_for_status()
    data = response.json()

    export_power = data['consumption'][1]['wNow']

    if isinstance(export_power, str):
        try:
            export_power = int(export_power)
        except ValueError:
            export_power = 0

    export_power = -export_power  # positive = excess solar
    #print(f"Current excess solar: {export_power}W")
    return export_power

def should_charge(soc, excess_solar, isCharging, chargeWatt):
    """
    Smart charging decision with battery health protection.
    Returns: (should_charge: bool, target_watts: int)
    """
    MAX_CHARGE_WATTS = 900
    MIN_CHARGE_WATTS = 200
    newChargeWatt = chargeWatt

    # Safety: Never charge above 95% SOC
    if soc >= 95:
        return False, newChargeWatt

    if isCharging:
        # === Currently charging ===
        if excess_solar < 50:
            # Solar is very low — try to reduce charge watts gradually
            if excess_solar + chargeWatt > 350:
                # Reduce by 100W steps while keeping some headroom
                for w in range(chargeWatt - 100, MIN_CHARGE_WATTS - 1, -100):
                    if excess_solar + w > 300:
                        newChargeWatt = max(MIN_CHARGE_WATTS, w)
                        return True, newChargeWatt
            # Not enough excess even at low watts → stop charging
            return False, MIN_CHARGE_WATTS

        # Good solar → increase charge rate if possible
        if excess_solar > 400 and chargeWatt < MAX_CHARGE_WATTS:
            newChargeWatt = min(MAX_CHARGE_WATTS, chargeWatt + 100)
            return True, newChargeWatt

        # Keep current rate
        return True, newChargeWatt

    else:
        # === Not currently charging ===
        if excess_solar > 300:
            # Start gently at 200W
            return True, MIN_CHARGE_WATTS

        return False, MIN_CHARGE_WATTS

# ====================== MAIN ======================
if __name__ == "__main__":
    print("🚀 Telegram Tester started")
    
    nz_time = datetime.now(ZoneInfo("Pacific/Auckland")).strftime('%Y-%m-%d %H:%M:%S %Z')
    quota = get_current_quota()
    #print(f"Current quota data: {quota}")
    soc = quota.get("pd.soc", 50)
    chargeWatt = quota.get("mppt.cfgChgWatts", 200)
    isCharging = quota.get("pd.watchIsConfig", 0) == 0
    excessSolar = get_excess_solar_watts()

    should_charge_result, newChargeWatt = should_charge(soc, excessSolar, isCharging, chargeWatt)   



    if excessSolar > 200:
        print("ready to charge with excess solar!")

    send_telegram(
        f"🚀 <b>Telegram Test Successful!</b>\n\n"
        f"Time in NZ: {nz_time}\n"
        f"Your Delta 2 controller is ready to go!"
        f"\n\nCurrent SOC: {soc}%"
        f"\nCurrent Charge Watts: {chargeWatt}W"
        f"\nIs Charging: {isCharging}"
        f"\nExcess Solar: {excessSolar}W"
        f"\n\nBased on the current SOC and excess solar, the controller thinks it should {'start' if should_charge_result else 'stop'} charging and set charge watts to {newChargeWatt}W."
    )