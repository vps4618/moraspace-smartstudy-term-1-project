// ! vps4618
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include "secrets.h" // Import the hidden credentials

//  ! vps4618
// The live URL provided by Render or your hosting platform
const char* serverUrl = "https://smartstudy-backend-psi.vercel.app/api/sensor/event";

#define TRIG_1 5   // Sensor 1 (Outside) Trigger
#define ECHO_1 18  // Sensor 1 (Outside) Echo
#define TRIG_2 19  // Sensor 2 (Inside) Trigger
#define ECHO_2 21  // Sensor 2 (Inside) Echo

// Detection threshold in centimeters (adjust based on doorway width)
const float DETECTION_DISTANCE_CM = 60.0; 

// Timeout in milliseconds to complete passing through both sensors
const unsigned long SEQUENCE_TIMEOUT_MS = 2500;

// ! vps4618
void sendSensorEvent(String eventType) {
  if (WiFi.status() == WL_CONNECTED) {
    
    WiFiClientSecure client;
    client.setInsecure(); // Skips strict SSL certificate validation for simplicity

    HTTPClient http;
    http.begin(client, serverUrl);

    // 1. Standard content type header
    http.addHeader("Content-Type", "application/json");

    // 2. Add your secret API key header here
    http.addHeader("X-API-Key", SECRET_API_KEY);
    
    // Format the JSON payload exactly as the FastAPI endpoint expects it
    String payload = "{\"area id\": \"apaya\", \"event\": \"" + eventType + "\"}";
    
    int httpResponseCode = http.POST(payload);
    
    if (httpResponseCode > 0) {
      Serial.printf("Success. HTTP Response code: %d\n", httpResponseCode);
    } else {
      Serial.printf("Failed. Error code: %d\n", httpResponseCode);
    }
    http.end();
  } else {
    Serial.println("Wi-Fi Disconnected from Hotspot");
  }
}

int studentCount = 0;

enum SequenceState {
  IDLE,
  WAITING_FOR_S2, // S1 triggered first -> expecting S2 (Entering)
  WAITING_FOR_S1  // S2 triggered first -> expecting S1 (Exiting)
};

SequenceState currentState = IDLE;
unsigned long stateStartTime = 0;

// Function to read distance from an HC-SR04 sensor
float readDistanceCM(int trigPin, int echoPin) {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  // Measure echo return time (timeout after ~30ms / ~5 meters)
  long duration = pulseIn(echoPin, HIGH, 30000);
  
  if (duration == 0) {
    return 999.0; // Return out-of-range if no echo received
  }
  
  // Speed of sound is ~0.0343 cm/microsecond
  return (duration * 0.0343) / 2.0;
}

void entered(){
    // ! vps4618
    sendSensorEvent("ENTER");
};
void exited(){
    // ! vps4618
    sendSensorEvent("EXIT");
};  

void setup() {
  Serial.begin(9600);

  // ! vps4618
  // 1. Use the variables from secrets.h to connect to the network
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to Wi-Fi");
  // 2. Wait until the connection is established
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nConnected to Wi-Fi");

  pinMode(TRIG_1, OUTPUT);
  pinMode(ECHO_1, INPUT);
  pinMode(TRIG_2, OUTPUT);
  pinMode(ECHO_2, INPUT);

  Serial.print("Current Student Count: ");
  Serial.println(studentCount);
}

void loop() {
  float dist1 = readDistanceCM(TRIG_1, ECHO_1);
  delay(15); // Small delay to avoid acoustic interference between sensors
  float dist2 = readDistanceCM(TRIG_2, ECHO_2);

  bool s1Triggered = (dist1 < DETECTION_DISTANCE_CM);
  bool s2Triggered = (dist2 < DETECTION_DISTANCE_CM);

  unsigned long currentTime = millis();

  switch (currentState) {
    case IDLE:
      if (s1Triggered && !s2Triggered) {
        currentState = WAITING_FOR_S2;
        stateStartTime = currentTime;
      } else if (s2Triggered && !s1Triggered) {
        currentState = WAITING_FOR_S1;
        stateStartTime = currentTime;
      }
      break;

    case WAITING_FOR_S2:
      // Person is moving: Outside -> Inside
      if (s2Triggered) {
        studentCount++;
        Serial.print("[ENTERED] Student Count: ");
        entered();
        Serial.println(studentCount);

        // Wait until door clearance before resuming idle
        while (readDistanceCM(TRIG_2, ECHO_2) < DETECTION_DISTANCE_CM) {
          delay(50);
        }
        currentState = IDLE;
        delay(200); // Debounce delay
      } else if (currentTime - stateStartTime > SEQUENCE_TIMEOUT_MS) {
        // Person walked away or didn't pass through
        currentState = IDLE;
      }
      break;

    case WAITING_FOR_S1:
      // Person is moving: Inside -> Outside
      if (s1Triggered) {
        if (studentCount > 0) {
          studentCount--;
        }
        Serial.print("[EXITED]  Student Count: ");
        exited();
        Serial.println(studentCount);

        // Wait until door clearance before resuming idle
        while (readDistanceCM(TRIG_1, ECHO_1) < DETECTION_DISTANCE_CM) {
          delay(50);
        }
        currentState = IDLE;
        delay(200); // Debounce delay
      } else if (currentTime - stateStartTime > SEQUENCE_TIMEOUT_MS) {
        // Person walked away or didn't pass through
        currentState = IDLE;
      }
      break;
  }

  delay(40); // Sampling rate buffer
}