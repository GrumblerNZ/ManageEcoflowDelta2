#!/usr/bin/env python3
"""
EcoFlow Delta 2 Smart Charger - SAFE PRODUCTION VERSION
- 6-hour MQTT credential caching
- 5-minute check interval during solar hours
- Very reliable long-term
"""

import json
import time
import requests
import random
import hashlib
import hmac
import paho.mqtt.client as mqtt
import ssl
import os
from datetime import datetime
from zoneinfo import ZoneInfo


import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ====================== YOUR ORIGINAL SIGNING ======================
def hmac_sha256(secret_key_string, message_string):
    secret_key_bytes = secret_key_string.encode('utf-8')
    message_bytes = message_string.encode('utf-8')
    return hmac.new(secret_key_bytes, message_bytes, hashlib.sha256).hexdigest()

def get_ecoflow_headers(access_key, secret_key, prefix=""):
    nonce = random.randint(100000, 999999)
    timestamp = int(time.time() * 1000)
    string_to_sign = prefix + f"accessKey={access_key}&nonce={nonce}&timestamp={timestamp}"
    signature = hmac_sha256(secret_key, string_to_sign)
    return {
        "accessKey": access_key,
        "nonce": str(nonce),
        "timestamp": str(timestamp),
        "sign": signature,
        "content-type": "application/json"
    }

# ====================== CONFIG ======================
with open("config/secrets.json") as f:
    secrets = json.load(f)

DELTA2_SN = secrets["ecoFlow"]["delta2"]
ECOFLOW_API_URL = secrets["ecoFlow"]["api_url"]

TELEGRAM_TOKEN = secrets.get("telegram", {}).get("bot_token")
TELEGRAM_CHAT_ID = secrets.get("telegram", {}).get("chat_id")

last_state = None
last_watts = 0

mqtt_creds = None
mqtt_creds_time = 0

def get_mqtt_credentials():
    """
    Get MQTT credentials with local file caching (refreshes every 2 hours)
    """
    cache_file = "mqtt_creds.json"
    max_age_seconds = 2 * 3600   # 2 hours

    # Try to load from cache
    if os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            creds = json.load(f)
        
        # Check if cache is still valid
        if time.time() - creds.get("timestamp", 0) < max_age_seconds:
            print("Using cached MQTT credentials")
            return creds
        else:
            print("MQTT credentials expired, fetching new ones...")

    # Cache is expired or doesn't exist → fetch new credentials
    nonce = str(random.randint(100000, 999999))
    timestamp = str(int(time.time() * 1000))
    msg = f"accessKey={secrets['ecoFlow']['access_key']}&nonce={nonce}&timestamp={timestamp}"
    sig = hmac.new(secrets["ecoFlow"]["secret_key"].encode(), msg.encode(), hashlib.sha256).hexdigest()

    headers = {
        "accessKey": secrets["ecoFlow"]["access_key"],
        "nonce": nonce,
        "timestamp": timestamp,
        "sign": sig,
    }

    url = f"{secrets['ecoFlow']['api_url']}/iot-open/sign/certification"
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if data.get("code") != "0":
        raise RuntimeError(f"MQTT cert error: {data}")

    creds = data["data"]
    creds["timestamp"] = time.time()   # Add timestamp for caching

    # Save to local file
    with open(cache_file, "w") as f:
        json.dump(creds, f, indent=2)

    print("New MQTT credentials saved to mqtt_creds.json")
    return creds


def publish_mqtt_command(sn: str, payload_dict: dict):
    creds = get_mqtt_credentials()
    command_topic = f"/open/{creds['certificateAccount']}/{sn}/set"
    payload = json.dumps(payload_dict)
    result = {"success": False, "error": None}

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            client.publish(command_topic, payload, qos=1)
        else:
            result["error"] = f"MQTT connect failed: {reason_code}"
            client.disconnect()

    def on_publish(client, userdata, mid, reason_code=None, properties=None):
        result["success"] = True
        client.disconnect()

    client = mqtt.Client(client_id=f"delta2-controller-{random.randint(1000,9999)}", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(creds["certificateAccount"], creds["certificatePassword"])
    client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)
    client.on_connect = on_connect
    client.on_publish = on_publish
    client.connect("mqtt-e.ecoflow.com", 8883, keepalive=30)
    client.loop_start()
    deadline = time.time() + 10
    while time.time() < deadline and not result["success"] and not result["error"]:
        time.sleep(0.1)
    client.loop_stop()
    
    if result["error"]:
        # One retry with fresh credentials
        creds = get_mqtt_credentials()
        client.username_pw_set(creds["certificateAccount"], creds["certificatePassword"])
        client.connect("mqtt-e.ecoflow.com", 8883, keepalive=30)
        client.loop_start()
        time.sleep(3)
        client.loop_stop()
    
    if result["error"]:
        raise RuntimeError(result["error"])
    return result

# ====================== TELEGRAM NOTIFICATIONS ======================
def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def notify_if_changed(new_state: str, soc: int, excess: int, target_watts: int = None):
    global last_state, last_watts
    if new_state == last_state:
        return
    last_state = new_state
    last_watts = target_watts or 0

    nz_tz = ZoneInfo("Pacific/Auckland")
    now = datetime.now(nz_tz).strftime('%H:%M NZST')

    if new_state == "AC":
        msg = f"🔌 <b>AC Charging ON</b>\nTime: {now}\nSOC: {soc}%\nExcess: {excess}W\nTarget: {target_watts or 200}W"
    else:
        msg = f"☀️ <b>Solar Priority Mode</b>\nTime: {now}\nSOC: {soc}%\nExcess: {excess}W"

    send_telegram(msg)

def notify_watt_change(new_watts: int, soc: int, excess: int):
    global last_watts
    if new_watts == last_watts:
        return
    last_watts = new_watts

    nz_tz = ZoneInfo("Pacific/Auckland")
    now = datetime.now(nz_tz).strftime('%H:%M NZST')
    msg = f"🔋 <b>Charging power changed to {new_watts}W</b>\nTime: {now}\nSOC: {soc}%\nExcess: {excess}W"
    send_telegram(msg)

# ====================== YOUR FUNCTIONS ======================
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
    response = requests.get(ENPHASE_API_URL, headers=headers, verify=False, timeout=30)
    response.raise_for_status()
    data = response.json()

    export_power = data['consumption'][1]['wNow']
    if isinstance(export_power, str):
        try:
            export_power = int(export_power)
        except ValueError:
            export_power = 0
    return -export_power

def should_charge(soc, excess_solar, isCharging, chargeWatt):
    MIN_START_EXCESS = 350
    MIN_CONTINUE_EXCESS = 150
    RAMP_UP_THRESHOLD = 500

    newChargeWatt = chargeWatt

    if soc >= 95:
        return False, newChargeWatt

    if isCharging:
        if excess_solar < MIN_CONTINUE_EXCESS:
            return False, 200
        if excess_solar > RAMP_UP_THRESHOLD and chargeWatt < 900:
            newChargeWatt = min(900, chargeWatt + 100)
            return True, newChargeWatt
        return True, newChargeWatt
    else:
        if excess_solar > MIN_START_EXCESS:
            return True, 200
        return False, 200

# ====================== MQTT FUNCTIONS ======================
def enable_backup_reserve(reserve_pct: int = 25, soc=0, excess=0, target_watts=0):
    notify_if_changed("AC", soc, excess, target_watts)
    return publish_mqtt_command(DELTA2_SN, {
        "id": str(int(time.time() * 1000)), "version": "1.0", "sn": DELTA2_SN,
        "moduleType": 1, "operateType": "watthConfig",
        "params": {"isConfig": 0, "bpPowerSoc": reserve_pct, "minDsgSoc": 0, "minChgSoc": 0}
    })

def disable_backup_reserve(current_reserve_pct: int = 25, soc=0, excess=0):
    notify_if_changed("Solar", soc, excess)
    return publish_mqtt_command(DELTA2_SN, {
        "id": str(int(time.time() * 1000)), "version": "1.0", "sn": DELTA2_SN,
        "moduleType": 1, "operateType": "watthConfig",
        "params": {"isConfig": 1, "bpPowerSoc": current_reserve_pct, "minDsgSoc": 0, "minChgSoc": 0}
    })

def set_ac_charging_power(watts: int, soc=0, excess=0):
    notify_watt_change(watts, soc, excess)
    creds = get_mqtt_credentials()
    command_topic = f"/open/{creds['certificateAccount']}/{DELTA2_SN}/set"
    clamped = max(200, min(1200, watts))

    payload = {
        "id": str(int(time.time() * 1000)),
        "version": "1.0",
        "sn": DELTA2_SN,
        "moduleType": 5,
        "operateType": "acChgCfg",
        "params": {
            "chgWatts": clamped,
            "chgPauseFlag": 255,
        }
    }
    return publish_mqtt_command(DELTA2_SN, payload)

def get_current_quota():
    try:
        headers = get_ecoflow_headers(secrets["ecoFlow"]["access_key"], secrets["ecoFlow"]["secret_key"])
        url = f"{ECOFLOW_API_URL}{secrets['ecoFlow']['get_device_quota_url']}/all?sn={DELTA2_SN}"
        resp = requests.get(url, headers=headers, timeout=15)
        if not resp.ok:
            print(f"❌ Quota failed: {resp.status_code}")
            return {}
        data = resp.json()
        if data.get("code") != "0":
            print(f"❌ API error: {data.get('message')}")
            return {}
        return data.get("data", {})
    except Exception as e:
        print(f"❌ Exception: {e}")
        return {}

# ====================== MAIN CONTROL LOGIC ======================
def control_charging():
    nz_tz = ZoneInfo("Pacific/Auckland")
    now = datetime.now(nz_tz)
    hour = now.hour
    minute = now.minute

    # 07:00 AM sharp - Force OFF
    if hour == 7 and minute == 0:
        print("07:00 AM → Force OFF")
        disable_backup_reserve(25, 0, 0)
        time.sleep(600) #10 minutes
        return

    # 16:00 (4:00 PM) sharp - Force OFF
    if hour == 16 and minute == 0:
        print("16:00 → Force OFF")
        disable_backup_reserve(25, 0, 0)
        time.sleep(900) #15 minutes
        return

    # Night charging: 21:00 → 07:00
    if hour >= 21 or hour < 7:
        print("Night mode → Force charging ON")
        enable_backup_reserve(30, 0, 0, 0)
        set_ac_charging_power(800, 0, 0)
        time.sleep(900) #15 minutes
        return

    # Smart solar window: 10:30 → 16:00 (every 5 minutes)
    if (hour == 10 and minute >= 30) or (11 <= hour <= 15) or (hour == 16 and minute < 0):
        quota = get_current_quota()
        if not quota:
            print("⚠️ Could not fetch quota")
            time.sleep(300)
            return

        soc = quota.get("pd.soc", 50)
        excess = get_excess_solar_watts()
        is_charging = quota.get("inv.inputWatts", 0) > 50
        current_charge_watt = quota.get("mppt.cfgChgWatts", 200)

        print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S %Z')}] SOC: {soc}% | Excess: {excess}W | Charging: {is_charging} ({current_charge_watt}W)")

        should_charge_now, target_watts = should_charge(soc, excess, is_charging, current_charge_watt)

        if should_charge_now:
            print(f"   → Charging at {target_watts}W")
            enable_backup_reserve(30, soc, excess, target_watts)
            set_ac_charging_power(target_watts, soc, excess)
        else:
            print("   → Solar only")
            disable_backup_reserve(25, soc, excess)

        time.sleep(60)  # 1 minute before next check
        return

    # Default periods → Solar only
    disable_backup_reserve(25, 0, 0)
    time.sleep(300)

# ====================== MAIN ======================
if __name__ == "__main__":
    print("🚀 EcoFlow Delta 2 Smart Charger started (SAFE VERSION)")
    send_telegram("🚀 <b>EcoFlow Delta 2 Controller started</b> (Safe 6-hour caching + 5-min checks)")

    while True:
        try:
            control_charging()
        except Exception as e:
            print(f"Error: {e}")
            send_telegram(f"⚠️ Error: {e}")
            time.sleep(300)