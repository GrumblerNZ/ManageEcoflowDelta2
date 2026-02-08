import hashlib
import json
import requests
import random
import time
import hmac

def hmac_sha256(secret_key_string, message_string):
    """
    Computes the HMAC-SHA256 signature for a given message and secret key.

    Args:
        secret_key_string (str): The secret key as a string.
        message_string (str): The message to be signed as a string.

    Returns:
        str: The HMAC-SHA256 signature as a hexadecimal string.
    """
    secret_key_bytes = secret_key_string.encode('utf-8')
    message_bytes = message_string.encode('utf-8')
    print(f"Secret Key: {secret_key_bytes}")
    print(f"Message: {message_bytes}")

    # Create the HMAC object using SHA256
    hmac_obj = hmac.new(secret_key_bytes, message_bytes, hashlib.sha256)

    # Get the hexadecimal representation of the digest
    signature = hmac_obj.hexdigest()
    return signature

def get_ecoflow_headers(access_key, secret_key, prefix=""):
    """
    Generates headers for Ecoflow API requests.

    Args:
        access_key (str): The Ecoflow access key.
        secret_key (str): The Ecoflow secret key.
        nonce (int): A random nonce value.
        timestamp (int): Current timestamp in milliseconds.

    Returns:
        dict: Headers for the Ecoflow API request.
    """
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

def process_Ecoflow_data(data, quota_url):
    """
    Processes Ecoflow data to extract relevant information.
    Expected response format:
    {'code': '0', 'message': 'Success', 'data': [{'sn': 'R601ZUB7XGBV0740', 'deviceName': 'RIVER 2 Wifi Lounge', 'online': 1, 'productName': 'RIVER 2'}, {'sn': 'R601ZUB7XGBV0946', 'deviceName': 'RIVER 2-Router', 'online': 1, 'productName': 'RIVER 2'}], 'eagleEyeTraceId': '9d48ae5324f033ce8ea148ba0a7d5b0a', 'tid': ''}
    
    Args:
        data (dict): The Ecoflow data as a dictionary.

    Returns:
        dict: Processed data with relevant information.
    """
    processed_data = {}
    # Example processing logic
    if 'data' in data:
        for device in data['data']:
            processed_data[device['sn']] = {
                'online': device['online'],
                'name': device['deviceName'],
                'product': device['productName']
            }
    query_all_url = quota_url + "/all?sn="

    # get all quota information
    for sn in processed_data:
        query_url = query_all_url + sn
        headers = get_ecoflow_headers(secrets["ecoFlow"]["access_key"], secrets["ecoFlow"]["secret_key"])
        response = requests.get(query_url, headers=headers)
        if response.ok:
            quota_data = response.json()
            print(f"Quota data for {sn}: {quota_data}")
            if 'data' in quota_data and len(quota_data['data']) > 0:
                ind = get_json_key_index(quota_data['data'], 'inv.cfgAcEnabled')
                print(f"Index of 'inv.cfgAcEnabled': {ind}")
                processed_data[sn]['quota'] = quota_data['data']
            else:
                processed_data[sn]['quota'] = None
        else:
            print(f"Failed to fetch quota for {sn}: {response.status_code} {response.text}")
    
    return processed_data

def get_json_key_index(json_data, key_to_find):
    """
    Recursively searches for a key in a nested JSON object and returns its index path.

    Args:
        json_data (dict or list): The JSON data to search.
        key_to_find (str): The key to find.

    Returns:
        list: A list of indices representing the path to the key, or None if not found.
    """
    index = None
    for i, key in enumerate(json_data.keys()):
        if key == key_to_find:
            index = i
            break
    return index 

def flatten_dict(obj, prefix=""):
    """Flatten nested dictionary for signing."""
    result = {}
    for key, value in obj.items():
        new_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(flatten_dict(value, new_key))
        else:
            result[new_key] = value
    return result


def set_charging_state(serial_number, enable):
    """Start or stop AC charging for the device."""
    data = {
        "sn": serial_number,
        "moduleType": 5,  # Typically for MPPT module
        "operateType": "acOutCfg",
        "params": {
            "enabled": 1 if enable else 0,  # 1 to enable charging, 0 to disable
            "xboost": 0,  # Optional: X-Boost setting
            "out_voltage": 230,  # Adjust based on your region/device
            "out_freq": 50  # Adjust based on your region/device
        }
    }
    return api_request("post", ecoflow_devicequota_url, data)

def set_ac_charging(serial_number, enable: bool, max_watts: int = None):
    """
    Enable/disable AC charging and optionally set max charging power (W).
    
    - enable=True  → start/allow AC charging
    - enable=False → pause/stop AC charging (usually by setting very low power or disable flag)
    
    Delta 2 typically allows 200–1200 W charging range.
    Some firmwares/APIs accept 0 to effectively disable charging.
    """
    params = {
        "chgPauseFlag": 0 if enable else 1,     # 0 = allow charging, 1 = pause charging
        # Alternative / additional style used in some integrations:
        # "enabled": 1 if enable else 0,
    }
    
    if max_watts is not None:
        # Many integrations use "acChgPower" or "chgPower" or similar
        params["acChgPower"] = max(200, min(1200, max_watts))   # clamp to valid range
    
    data = {
        "sn": serial_number,
        "moduleType": 1,               # ← most common for main BMS / charging control on Delta 2
        # moduleType 5 is often MPPT/solar related, not AC charging
        "operateType": "acChgCfg",     # ← this is usually the correct one for AC input/charging
        "params": params
    }
    
    # If the above doesn't work, also try these variants one at a time:
    # operateType: "chgMode" / "setChg" / "acCharge"
    # moduleType: 0 or 2 or leave it out completely (some requests omit it)
    # params keys: "chgWatts", "chargePower", "maxChgWatt", "chgPower"
    
    return api_request("post", ecoflow_devicequota_url, data)

def api_request(method, endpoint, data=None):
    """Make an API request to EcoFlow."""
    nonce = str(100000 + random.randint(0, 99999))
    timestamp = str(int(time.time() * 1000))
    url = f"{endpoint}"
    
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "accessKey": ecoflow_access_key,
        "nonce": nonce,
        "timestamp": timestamp,
        "sign": generate_signature(data, nonce, timestamp, ecoflow_secret_key)
    }
    
    try:
        if method.lower() == "post":
            response = requests.post(url, json=data, headers=headers)
        else:
            raise ValueError("Only POST method is supported for this endpoint")
        
        response.raise_for_status()
        result = response.json()
        
        if result.get("code") == 0:
            return result.get("data")
        else:
            raise Exception(f"API Error: {result.get('message')}")
            
    except requests.RequestException as e:
        raise Exception(f"Request failed: {str(e)}")


def generate_signature(data, nonce, timestamp, secret_key):
    """Generate HMAC-SHA256 signature for the API request."""
    # Flatten data for signing
    flat_data = {}
    if data:
        flat_data = flatten_dict(data  )
    sorted_keys = sorted(flat_data.keys())
    data_str = "&".join(f"{key}={flat_data[key]}" for key in sorted_keys)
    sign_str = f"{data_str}&accessKey={ecoflow_access_key}&nonce={nonce}&timestamp={timestamp}"
    signature = hmac.new(
        secret_key.encode("utf-8"),
        sign_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    #print(f"{sign_str}")
    print(f"Secret Key: {secret_key}")
    #print(f"Generated signature 1: {signature}")

    #signature = hmac_sha256(secret_key, sign_str)
    print(f"Generated signature 2: {signature}")
    return signature


with open("./config/secrets.json") as f:
    secrets = json.load(f)

username = secrets["username"]
password = secrets["password"]


envoy_serial = secrets["serial_number"]
data = {'user[email]': username, 'user[password]': password}
response = requests.post('http://enlighten.enphaseenergy.com/login/login.json?',
data=data) 
response_data = json.loads(response.text)

#print(response_data)
data = {'session_id': response_data['session_id'], 'serial_num': envoy_serial, 'username':username}
response = requests.post('http://entrez.enphaseenergy.com/tokens', json=data)
token_raw = response.text

#token_raw = secrets['access_token']
#print(token_raw)

ENPHASE_API_URL = secrets["api_url"]
headers = {"Authorization": f"Bearer {token_raw}", "Accept": "application/json"}
response = requests.get(ENPHASE_API_URL, headers=headers, verify=False, timeout=30)
response.raise_for_status()
data = response.json()
# Adjust key path according to your Envoy firmware version
export_power = data['consumption'][1]['wNow']  # Assuming [1] is net export
print(f"Current consumption power: {export_power}")


# Ecoflow API URL and headers
ECOFLOW_API_URL = secrets["ecoFlow"]["api_url"]
# get device list from Ecoflow
ecoflow_device_list_url = f"{ECOFLOW_API_URL}{secrets['ecoFlow']['get_device_info_url']}"
ecoflow_devicequota_url = f"{ECOFLOW_API_URL}{secrets['ecoFlow']['get_device_quota_url']}"

"""
secretKey="WIbFEKre0s6sLnh4ei7SPUeYnptHG6V"
ecoflow_access_key = "Fp4SvIprYSDPXtYJidEtUAd1o"
teststr = "params.cmdSet=11&params.eps=0&params.id=24&sn=123456789&accessKey=Fp4SvIprYSDPXtYJidEtUAd1o&nonce=345164&timestamp=1671171709428"
nonce=345164
test_data = {
    "sn" : "123456789" ,
    "params" :{
        "cmdSet" :11,
        "id" :24,
        "eps" :0
    }
}
timestamp=1671171709428
print(teststr)
test_sn = generate_signature(test_data, nonce, timestamp, secretKey)
print(f"Test SN: {test_sn}")
# expected output: 07c13b65e037faf3b153d51613638fa80003c4c38d2407379a7f52851af1473e
"""

ecoflow_access_key = secrets["ecoFlow"]["access_key"]
ecoflow_secret_key = secrets["ecoFlow"]["secret_key"]
export_power = -100

# Headers
headers = get_ecoflow_headers(ecoflow_access_key, ecoflow_secret_key)
print(headers)
# Query device quota endpoint
print(ecoflow_device_list_url)
if export_power < 0:
    # If export power is positive, enable AC charging
    print("Enabling AC charging...")
    response = set_ac_charging("R331ZUB5SGC90073", True, 200)
    print(f"Response from Ecoflow API: {response}")
else:
    response = set_ac_charging("R331ZUB5SGC90073", False)
    print(f"Response from Ecoflow API: {response}")

""" # confirmed working with HMAC SHA256 signature generation
## signature generation for Ecoflow API TEST - HMAC SHA256
secretKey="WIbFEKre0s6sLnh4ei7SPUeYnptHG6V"
teststr = "params.cmdSet=11&params.eps=0&params.id=24&sn=123456789&accessKey=Fp4SvIprYSDPXtYJidEtUAd1o&nonce=345164&timestamp=1671171709428"
test_sn = hmac_sha256(secret_key_string=secretKey, message_string=teststr)
print(f"Test SN: {test_sn}")
# expected output: 07c13b65e037faf3b153d51613638fa80003c4c38d2407379a7f52851af1473e
"""






"""
# Make the GET request
response = requests.get(ecoflow_device_list_url, headers=headers)

# Check status and print result
if response.ok:
    data = response.json()
    devices = process_Ecoflow_data(data, ecoflow_devicequota_url)
    #print("Processed Ecoflow devices:", devices)
    for sn, device in devices.items():
        print(f"SN: {sn}, Name: {device['name']}, Online: {device['online']}, Product: {device['product']}")
    #print("Device status:")
    #print(data)
else:
    print("Request failed:", response.status_code, response.text)

"""    