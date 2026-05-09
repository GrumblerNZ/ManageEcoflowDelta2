# EnphaseToEcoflow

A simple Python script project.

## Getting Started

1. Ensure you have Python 3.x installed.
2. (Optional) Create a virtual environment:
   ```sh
   python3 -m venv venv
   source venv/bin/activate
   ```
3. Install dependencies:
   ```sh
   pip install -r requirements.txt
   ```
4. Run the main script:
   ```sh
   python main.py
   ```
# ManageEcoflowDelta2


## secret.json Sample
{
  "username": "your.email@whatever.com",
  "password": "Password_For_Enphase",
  "serial_number": "123456789012",
  "auth_url": "https://enlighten.enphaseenergy.com/entrez-auth-token?serial_num=",
  "api_url": "https://envoy.local/production.json",
  "access_token": "YOUR_JWT_TOKEN",
  "ecoFlow": {
    "access_key": "YourEcoflowAccessKey",
    "secret_key": "YourEcoflowSecretKey",
    "api_url": "https://api-e.ecoflow.com",
    "device_list": ["R6SERIAL12345", "R6SERIAL89343485"],
    "delta2": "DELTA2SERIAL",
    "get_device_info_url": "/iot-open/sign/device/list",
    "get_device_quota_url": "/iot-open/sign/device/quota",
    "set_device_quota_url": "/iot-open/sign/device/quota",
    "get_certification_url": "/iot-open/sign/certification"

  },
  "telegram": {
  "bot_token": "7291384753:AKSJDFHAKSJDHFKJL_YALSKJFDas",
  "chat_id": "1231231230"
  }
}


