#!/usr/bin/env python3
"""
EcoFlow Delta 2 Smart Charger - PRODUCTION VERSION
- ONE persistent MQTT connection (best practice)
- 2-hour credential caching
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

# ====================== PERSISTENT MQTT CLIENT ======================
mqtt_client = None
mqtt_creds = None
mqtt_creds_time = 0

def get_mqtt_credentials(force_refresh=False):
    global mqtt_creds, mqtt_creds_time
    
    if not force_refresh and mqtt_creds and (time.time() - mqtt_creds_time) < 21600:  # 6 hours
        return mqtt_creds
    
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
    
    url = f"{ECOFLOW_API_URL}/iot-open/sign/certification"
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    
    if data.get("code") != "0":
        raise RuntimeError(f"MQTT cert error: {data}")
    
    mqtt_creds = data["data"]
    mqtt_creds_time = time.time()
    return mqtt_creds

def init_mqtt_connection():
    """Initialize ONE persistent MQTT connection"""
    global mqtt_client
    
    if mqtt_client and mqtt_client.is_connected():
        return mqtt_client
    
    creds = get_mqtt_credentials()
    
    mqtt_client = mqtt.Client(
        client_id=f"delta2-persistent-{random.randint(1000,9999)}",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    mqtt_client.username_pw_set(creds["certificateAccount"], creds["certificatePassword"])
    mqtt_client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLS)
    
    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print("✅ MQTT connected successfully (persistent connection)")
        else:
            print(f"❌ MQTT connection failed: {reason_code}")
    
    def on_disconnect(client, userdata, flags, reason_code, properties):
        print("⚠️ MQTT disconnected - will reconnect automatically")
    
    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    
    broker = creds.get("url", "mqtt-e.ecoflow.com")
    port = int(creds.get("port", 8883))
    
    print(f"Connecting to MQTT broker {broker}:{port} (persistent)...")
    mqtt_client.connect(broker, port, keepalive=60)
    mqtt_client.loop_start()
    
    # Wait for connection
    time.sleep(3)
    return mqtt_client

def publish_mqtt_command(sn: str, payload_dict: dict):
    """Send command using the persistent connection"""
    global mqtt_client
    
    if not mqtt_client or not mqtt_client.is_connected():
        print("Reconnecting MQTT...")
        init_mqtt_connection()
    
    creds = get_mqtt_credentials()
    command_topic = f"/open/{creds['certificateAccount']}/{sn}/set"
    payload = json.dumps(payload_dict)
    
    result = mqtt_client.publish(command_topic, payload, qos=1)
    
    if result.rc != 0:
        raise RuntimeError(f"MQTT publish failed: {result.rc}")
    
    return True

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
    """
    Smart charging logic - NO SOC safety check.
    Uses true_available = chargeWatt + excess_solar for accurate decision making.
    """
    MIN_CHARGE = 200
    MAX_CHARGE = 1200
    BUFFER = 100   # Leave ~100W buffer for house loads

    if isCharging:
        # === Already charging ===
        true_available = chargeWatt + excess_solar
        new_target = true_available - BUFFER
        new_target = max(MIN_CHARGE, min(MAX_CHARGE, new_target))
        new_target = (new_target // 100) * 100   # Round to nearest 100

        if new_target < MIN_CHARGE:
            return False, MIN_CHARGE
        return True, new_target

    else:
        # === Not currently charging ===
        if excess_solar > 350:
            new_target = excess_solar - 200
            new_target = max(MIN_CHARGE, min(MAX_CHARGE, new_target))
            new_target = (new_target // 100) * 100
            return True, new_target
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

    # === 07:00 AM sharp - Force OFF (Solar only) ===
    if hour == 7 and minute == 0:
        quota = get_current_quota()
        is_charging = quota.get("inv.inputWatts", 0) > 50 if quota else False

        if is_charging:
            print("07:00 AM → Force OFF (Solar only)")
            disable_backup_reserve(25, 0, 0)
        else:
            print("07:00 AM → Status quo: Already Solar only")
        time.sleep(600)
        return

    # === 16:00 (4:00 PM) sharp - Force OFF (Solar only) ===
    if hour == 16 and minute == 0:
        quota = get_current_quota()
        is_charging = quota.get("inv.inputWatts", 0) > 50 if quota else False

        if is_charging:
            print("16:00 → Force OFF (Solar only)")
            disable_backup_reserve(25, 0, 0)
        else:
            print("16:00 → Status quo: Already Solar only")
        time.sleep(900)
        return

    # === Night charging: 21:00 → 07:00 ===
    if hour >= 21 or hour < 7:
        quota = get_current_quota()
        is_charging = quota.get("inv.inputWatts", 0) > 50 if quota else False
        current_charge_watt = quota.get("mppt.cfgChgWatts", 200) if quota else 0

        if not is_charging or abs(current_charge_watt - 800) > 50:
            print(f"[{now.strftime('%H:%M')}] Night mode → Force charging ON at 800W")
            enable_backup_reserve(30, 0, 0, 0)
            set_ac_charging_power(800, 0, 0)
        else:
            print(f"[{now.strftime('%H:%M')}] Night mode → Status quo: Charging at {current_charge_watt}W")
        
        time.sleep(900)
        return

        # === Smart solar window: 10:30 → 16:00 ===
    if (hour == 10 and minute >= 30) or (11 <= hour <= 15) or (hour == 16 and minute < 0):
        quota = get_current_quota()
        if not quota:
            print(f"[{now.strftime('%H:%M')}] ⚠️ Could not fetch quota")
            time.sleep(300)
            return

        soc = quota.get("pd.soc", 50)
        excess = get_excess_solar_watts()
        is_charging = quota.get("inv.inputWatts", 0) > 50
        current_charge_watt = quota.get("mppt.cfgChgWatts", 200)

        print(f"\n[{now.strftime('%Y-%m-%d %H:%M:%S %Z')}] SOC: {soc}% | Excess: {excess}W | Charging: {is_charging} ({current_charge_watt}W)")

        should_charge_now, target_watts = should_charge(soc, excess, is_charging, current_charge_watt)

        # === Status Quo Check ===
        if should_charge_now:
            if not is_charging:
                # Transitioning from Solar → AC charging
                print(f"[{now.strftime('%H:%M')}]    → Starting AC charging at {target_watts}W")
                enable_backup_reserve(30, soc, excess, target_watts)
                set_ac_charging_power(target_watts, soc, excess)
            else:
                # Already charging — check if we really need to change the power
                if abs(target_watts - current_charge_watt) >= 50:
                    print(f"[{now.strftime('%H:%M')}]    → Changing charge rate from {current_charge_watt}W to {target_watts}W")
                    set_ac_charging_power(target_watts, soc, excess)
                else:
                    print(f"[{now.strftime('%H:%M')}]    → Status quo: Charging at {current_charge_watt}W (no change needed)")
        else:
            if is_charging:
                print(f"[{now.strftime('%H:%M')}]    → Stopping charging (Solar only)")
                disable_backup_reserve(25, soc, excess)
            else:
                print(f"[{now.strftime('%H:%M')}]    → Status quo: Solar only (no change needed)")

        time.sleep(60)  # 1 minute before next check
        return

    # Default periods → Solar only
    disable_backup_reserve(25, 0, 0)
    time.sleep(300)

def shutdown_mqtt():
    """Call this on clean exit."""
    global mqtt_client, mqtt_connected
    if mqtt_client:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        mqtt_client = None
        mqtt_connected = False

# ====================== MAIN ======================
if __name__ == "__main__":
    print("🚀 EcoFlow Delta 2 Smart Charger started (SAFE VERSION)")
    send_telegram("🚀 <b>EcoFlow Delta 2 Controller started</b> (Safe 6-hour caching + 5-min checks)")

try:
    while True:
        try:
            control_charging()
        except Exception as e:
            print(f"Error: {e}")
            send_telegram(f"⚠️ Error: {e}")
            time.sleep(300)
except KeyboardInterrupt:
    print("\n🛑 Shutting down gracefully (CTRL+C pressed)...")
finally:
    shutdown_mqtt()
    print("✅ MQTT connection closed cleanly.")