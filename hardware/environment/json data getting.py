import json
import os
import socket
import requests
import threading
import time
from dotenv import load_dotenv

load_dotenv()

ESP32_HOST = "esp32sensors.local"
PORT = 8888

API_URL = "http://smartstudy-backend-psi.vercel.app/api/env/sync" 
API_KEY = os.getenv("API_KEY")

if not API_KEY:
    raise ValueError("Error: API_KEY is missing. Please set it in your .env file.")

def send_env_update(payload):
    try:
        response = requests.post(API_URL, json=payload, headers={"X-API-Key": API_KEY}, timeout=5)
        print(f"Forwarded to backend: Status {response.status_code}")
    except Exception as e:
        print(f"Env data sync failed: {e}")

def listen_and_forward():
    print(f"Starting continuous polling of ESP32 at {ESP32_HOST}:{PORT}...")

    while True:
        try:
            # 1. Open connection
            client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client_socket.settimeout(5)
            client_socket.connect((ESP32_HOST, PORT))
            
            # 2. Wait for the ESP32 to send its 3-second JSON payload
            data = client_socket.recv(1024)
            
            if data:
                buffer = data.decode("utf-8")
                for line in buffer.split("\n"):
                    line = line.strip()
                    if line:
                        try:
                            raw_data = json.loads(line)
                            rssi = raw_data.get("wifi_rssi", -100)
                            is_wifi_good = rssi > -85 
                            
                            payload = {
                                "area_id": "apaya",
                                "aqi": int(raw_data.get("aqi", 50)),
                                "temp": raw_data.get("temp", 25.0),
                                "feels_like": raw_data.get("temp", 25.0) + (raw_data.get("hum", 50) * 0.05),
                                "humidity": raw_data.get("hum", 60.0),
                                "noise": raw_data.get("noise_db", 45.0),
                                "wifi": is_wifi_good,
                                "power": True 
                            }
                            
                            # Send data in the background without blocking the next socket connection
                            threading.Thread(target=send_env_update, args=(payload,), daemon=True).start()
                            
                        except json.JSONDecodeError:
                            pass
                            
            # 3. Close the socket and wait briefly before connecting again
            client_socket.close()
            time.sleep(1)
            
        except Exception as e:
            print(f"Socket connection failed: {e}. Retrying in 3 seconds...")
            time.sleep(3)

if __name__ == "__main__":
    listen_and_forward()