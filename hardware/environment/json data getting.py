import json
import os  # 1. Import os
import socket
import requests
from dotenv import load_dotenv  # 2. Import load_dotenv

# 3. Load environment variables from the .env file
load_dotenv()

ESP32_HOST = "esp32sensors.local"
PORT = 8888

# Change to your Vercel URL if testing in production
API_URL = "http://smartstudy-backend-psi.vercel.app/api/env/sync" 
# 4. Fetch the API key safely. If not found, it defaults to None.
API_KEY = os.getenv("API_KEY")


if not API_KEY:
    raise ValueError("Error: API_KEY is missing. Please set it in your .env file.")
                     
def listen_and_forward():
    print(f"Connecting to ESP32 at {ESP32_HOST}:{PORT}...")
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_socket.connect((ESP32_HOST, PORT))
    print("Connected! Forwarding data to FastAPI...")

    buffer = ""
    while True:
        data = client_socket.recv(1024)
        if not data:
            break
        buffer += data.decode("utf-8")
        
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.strip()
            if line:
                try:
                    raw_data = json.loads(line)
                    rssi = raw_data.get("wifi_rssi", -100)
                    is_wifi_good = rssi > -85  # -85 dBm is the threshold for a usable connection
                    
                    # Map ESP32 keys to your EnvTelemetry schema
                    payload = {
                        "area_id": "apaya", # Assuming this sensor is in Apaya
                        "aqi": int(raw_data.get("aqi", 50)),
                        "temp": raw_data.get("temp", 25.0),
                        "feels_like": raw_data.get("temp", 25.0) + (raw_data.get("hum", 50) * 0.05), # Rough heat index mock
                        "humidity": raw_data.get("hum", 60.0),
                        "noise": raw_data.get("noise_db", 45.0),
                        
                        "wifi": is_wifi_good,
                        "power": True  # If we receive data, it has power
                    }
                    
                    response = requests.post(
                        API_URL, 
                        json=payload, 
                        headers={"X-API-Key": API_KEY}
                    )
                    print(f"Forwarded to backend: Status {response.status_code}")
                    
                except json.JSONDecodeError:
                    pass

if __name__ == "__main__":
    listen_and_forward()