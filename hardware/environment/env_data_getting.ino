#include <Wire.h>
#include <Adafruit_AHTX0.h>
#include "ScioSense_ENS160.h"
#include <Arduino.h>
#include "driver/i2s.h"
#include "driver/adc.h"
#include <WiFi.h>
#include <ESPmDNS.h>
#include <math.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include "secrets.h" // Your file with WIFI_SSID, WIFI_PASSWORD, and SECRET_API_KEY

#define LED_PIN 32   //sharp sensor
#define VO_PIN 34    // Corresponds to ADC1 Channel 6 (GPIO 34)

#define SDA_PIN 21   //ens+bme
#define SCL_PIN 22

#define I2S_WS   25  //microphone
#define I2S_SCK  26
#define I2S_SD   33

#define I2S_PORT I2S_NUM_0

#define SAMPLE_RATE 16000
#define SAMPLE_COUNT 1024
//--------------web----------------------

const char* serverUrl = "https://smartstudy-backend-psi.vercel.app/api/env/sync";

// FreeRTOS queue to hold JSON strings waiting to be sent
QueueHandle_t envDataQueue = nullptr;
//const char* SECRET_API_KEY =  "";

// Create background HTTP Task
void backendRequestTask(void *parameter) {
  String* payloadPtr; // Use a pointer

  for (;;) {
    // Wait for a pointer to enter the queue
    if (xQueueReceive(envDataQueue, &payloadPtr, portMAX_DELAY) == pdTRUE) {
      if (WiFi.status() == WL_CONNECTED) {
        WiFiClientSecure client;
        client.setInsecure(); // Skip strict SSL

        HTTPClient http;
        http.begin(client, serverUrl);
        http.addHeader("Content-Type", "application/json");
        http.addHeader("X-API-Key", SECRET_API_KEY);
        
        // Dereference the pointer with * to get the string
        int httpResponseCode = http.POST(*payloadPtr);
        
        if (httpResponseCode > 0) {
          Serial.printf("Env Sync Success: %d\n", httpResponseCode);
        } else {
          Serial.printf("Env Sync Failed: %d\n", httpResponseCode);
        }
        http.end();
      }
      // CRITICAL: Free the heap memory so the ESP32 doesn't run out of RAM
      delete payloadPtr;
    }
  }
}






// ---------- Wi-Fi Credentials ----------
//const char* ssid = "";
//const char* password = "";
//WiFiServer server(8888);

Adafruit_AHTX0 aht;       //ens+bme

ScioSense_ENS160 ens160(ENS160_I2CADDR_1);  // 0x53


// ---------- I2C Bus Recovery Function ----------
void clearI2cBus() {
  pinMode(SDA_PIN, INPUT_PULLUP);
  pinMode(SCL_PIN, OUTPUT);
  for (int i = 0; i < 16; i++) {
    digitalWrite(SCL_PIN, LOW);
    delayMicroseconds(5);
    digitalWrite(SCL_PIN, HIGH);
    delayMicroseconds(5);
  }
  pinMode(SDA_PIN, INPUT);
  pinMode(SCL_PIN, INPUT);
}





// ---------- Custom Study Room Air Quality Score ----------

// Keep a score within 0-100%
float clampScore(float score) {
  return constrain(score, 0.0f, 100.0f);
}

// Interpolate a score between two measurement thresholds
float interpolateScore(float value,
                       float x1, float score1,
                       float x2, float score2) {
  if (value <= x1) return score1;
  if (value >= x2) return score2;

  return score1 + (value - x1) *
         (score2 - score1) / (x2 - x1);
}

// Dust score based on your Sharp sensor calibration
float dustScore(float dust) {
  if (dust <= 5)   return 100;
  if (dust <= 10)  return interpolateScore(dust, 5, 95, 10, 80);
  if (dust <= 35)  return interpolateScore(dust, 10, 80, 35, 60);
  if (dust <= 55)  return interpolateScore(dust, 35, 60, 55, 40);
  if (dust <= 125) return interpolateScore(dust, 55, 40, 125, 15);

  return interpolateScore(dust, 125, 15, 200, 0);
}

// eCO2 score (ppm)
// eCO2 is an estimate, not a direct CO2 measurement.
float eco2Score(float eco2) {
  if (eco2 <= 600)  return 100;
  if (eco2 <= 1000) return interpolateScore(eco2, 600, 100, 1000, 80);
  if (eco2 <= 1500) return interpolateScore(eco2, 1000, 80, 1500, 50);
  if (eco2 <= 2500) return interpolateScore(eco2, 1500, 50, 2500, 20);

  return interpolateScore(eco2, 2500, 20, 5000, 0);
}

// TVOC score (ppb)
// Initial design thresholds; calibrate for your environment.
float tvocScore(float tvoc) {
  if (tvoc <= 65)  return 100;
  if (tvoc <= 220) return interpolateScore(tvoc, 65, 100, 220, 75);
  if (tvoc <= 660) return interpolateScore(tvoc, 220, 75, 660, 40);
  if (tvoc <= 2200) return interpolateScore(tvoc, 660, 40, 2200, 0);

  return 0;
}

// ENS160 AQI: 1 = best, 5 = worst
float ensAqiScore(float aqi) {
  if (aqi < 1 || aqi > 5) return 0;

  return (5.0f - aqi) * 25.0f;
}

// Humidity score (%)
// Target range: 40-60% RH
float humidityScore(float hum) {
  if (hum >= 40 && hum <= 60) return 100;

  if (hum < 40)
    return interpolateScore(hum, 20, 0, 40, 100);

  return interpolateScore(hum, 60, 100, 90, 0);
}

// Combine all sensor scores into one percentage
float calculateStudyAQI(float hum,
                        float eco2,
                        float tvoc,
                        float aqi,
                        float dust) {

  float dust_s = dustScore(dust);
  float eco2_s = eco2Score(eco2);
  float tvoc_s = tvocScore(tvoc);
  float aqi_s = ensAqiScore(aqi);
  float hum_s = humidityScore(hum);

  float total =
      dust_s * 0.35f +
      eco2_s * 0.25f +
      tvoc_s * 0.20f +
      aqi_s * 0.15f +
      hum_s * 0.05f;

  return clampScore(total);
}

String getAQILabel(float score) {
  if (score >= 90) return "Excellent";
  else if (score >= 75) return "Good";
  else if (score >= 60) return "Moderate";
  else if (score >= 40) return "Poor";
  else return "Very Poor";
}




void setup() {
  Serial.begin(115200);

  // ---------- Wi-Fi Setup ----------
  WiFi.begin(ssid, password);
  Serial.print("Connecting to WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected.");
  //-------------------------------------------
  // Buffer sensor events and send them in the background
  envDataQueue = xQueueCreate(5, sizeof(String*));
  if (envDataQueue != nullptr) {
    xTaskCreate(
      backendRequestTask,
      "EnvBackendTask",
      8192,
      nullptr,
      1,
      nullptr
    );
  } else {
    Serial.println("Failed to create backend event queue.");
  }


  // ---------- mDNS Setup ----------
  if (!MDNS.begin("esp32sensors")) {
    Serial.println("Error setting up MDNS responder!");
  } else {
    Serial.println("mDNS responder started: esp32sensors.local");
  }

  //server.begin();

  // Clear stuck I2C bus lines before initializing Wire
  clearI2cBus();
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000); // Slow down I2C to 100kHz for high stability and noise immunity

  // ---------- AHT21 ----------
  if (aht.begin()) {
    Serial.println("AHT21 detected.");
  } else {
    Serial.println("AHT21 not detected.");
  }

  // ---------- ENS160 ----------
  if (ens160.begin()) {
    Serial.println("ENS160 detected.");
    ens160.setMode(ENS160_OPMODE_STD);
  } else {
    Serial.println("ENS160 not detected.");
  }

  Serial.println("\nStarting measurements...\n");

  //ens setup is above

  i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
    .sample_rate = SAMPLE_RATE,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
    .communication_format = I2S_COMM_FORMAT_I2S,
    .intr_alloc_flags = 0,
    .dma_buf_count = 8,
    .dma_buf_len = 256,
    .use_apll = false,
    .tx_desc_auto_clear = false,
    .fixed_mclk = 0
  };

  i2s_pin_config_t pin_config = {
    .bck_io_num = I2S_SCK,
    .ws_io_num = I2S_WS,
    .data_out_num = I2S_PIN_NO_CHANGE,
    .data_in_num = I2S_SD
  };

  i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL);
  i2s_set_pin(I2S_PORT, &pin_config);
  i2s_zero_dma_buffer(I2S_PORT);
  i2s_start(I2S_PORT);

  Serial.println("INMP441 noise level meter started");


  // microphone i2s setup is above

  //sharp setup.
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);

  // Configure native IDF ADC to prevent legacy driver conflict with new I2S driver
  adc1_config_width(ADC_WIDTH_BIT_12);
  adc1_config_channel_atten(ADC1_CHANNEL_6, ADC_ATTEN_DB_12); // GPIO 34 is ADC1_CHANNEL_6, ~3.3V full-scale range




  // put your setup code here, to run once:

}

void loop() {

  // ---------- Variables for averaging ----------

  float tempSum = 0;
  float humSum = 0;

  // ENS160
  float eco2Sum = 0;
  float tvocSum = 0;
  float aqiSum = 0;

  int ensSamples = 0;

  const int samples = 5;


  // =====================================================
  // TAKE 5 SAMPLES
  // =====================================================

  for (int i = 0; i < samples; i++) {

    // ---------- AHT21 ----------

    sensors_event_t humidity;
    sensors_event_t temperature;

    aht.getEvent(&humidity, &temperature);

    tempSum += temperature.temperature;
    humSum += humidity.relative_humidity;


    // ---------- ENS160 ----------

    // Give ENS160 the temperature and humidity
    // from the AHT21.
    ens160.set_envdata(
      temperature.temperature,
      humidity.relative_humidity
    );

    if (ens160.available()) {

      ens160.measure(true);

      eco2Sum += ens160.geteCO2();
      tvocSum += ens160.getTVOC();
      aqiSum += ens160.getAQI();

      ensSamples++;
    }

    delay(100);
  }


  // =====================================================
  // CALCULATE AVERAGES
  // =====================================================

  float temp = tempSum / samples;
  float hum = humSum / samples;


  // ---------- ENS160 averages ----------

  float eco2 = 0;
  float tvoc = 0;
  float aqi = 0;

  if (ensSamples > 0) {

    eco2 = eco2Sum / ensSamples;
    tvoc = tvocSum / ensSamples;
    aqi = aqiSum / ensSamples;
  }
  delay(50);

  //i2s microphone code start here

  int cont = 0;
  double values[5] = {0};
  static int32_t i2s_samples[SAMPLE_COUNT];

  while(cont < 5){

    size_t bytesRead;

    i2s_read(
      I2S_PORT,
      i2s_samples,
      sizeof(i2s_samples),
      &bytesRead,
      portMAX_DELAY
    );

    int sampleCount = bytesRead / sizeof(int32_t);

    if (sampleCount > 0) {
      // Calculate RMS
      double sumSquares = 0;

      for (int i = 0; i < sampleCount; i++) {

        // INMP441 data is 24-bit stored inside 32-bit
        int32_t sample = i2s_samples[i] >> 8;

        sumSquares += (double)sample * sample;
      }

      double rms = sqrt(sumSquares / sampleCount);

       // Convert RMS to dBFS
      double dbFS = 20.0 * log10(rms / 8388607.0);
      values[cont] = dbFS;

      //Serial.print("RMS: ");
      //Serial.print(rms, 0);

      //Serial.print("     Level: ");
      //Serial.print(dbFS, 2);

      //Serial.println(" dBFS");
      cont++;
    }

    delay(100);
  }

  double sum = 0;
  for (int i =0; i < 5; i++){
    sum = sum + values[i];
  }
  double avg = sum/5;
  //Serial.println(avg);
  double fin = avg + 80;// calibrated -45 to 30dB+5

  delay(50);

  // sharp sensor start here.


  float dust[5];
  int count=0;
  while (count < 5) {

    // Turn IR LED ON
    digitalWrite(LED_PIN, LOW);
    delayMicroseconds(280);

    int adc = adc1_get_raw(ADC1_CHANNEL_6); // Replaced legacy analogRead to fix driver conflict crash

    // Turn IR LED OFF
    digitalWrite(LED_PIN, HIGH);

    // Voltage at ESP32 ADC
    float adcVoltage = adc * 3.3 / 4095.0;

    // Undo your 1k/2k divider
    float sensorVoltage = adcVoltage * 1.5;

    // Clean-air baseline
    float cleanVoltage = 0.035;

    float dustDensity =  (sensorVoltage - cleanVoltage) / 0.005;  // 0.005 is the typical sensitivity

    // Prevent negative values
    if (dustDensity < 0) {
       dustDensity = 0;
    }

    dust[count] = dustDensity;
    count++;
    delay(100);

  }
  float avgr = (dust[0]+dust[1]+dust[2]+dust[3]+dust[4])/5;

  delay(50);


  //Serial.print("Dust voltage: ");
  //Serial.print(sensorVoltage, 3);

  //Serial.print(" V | Dust level: ");
  //Serial.print(dustDensity, 2);

  //Serial.println(" mg/m3");

  //delay(10);


  //sharp sensor calibrations,
  // 0-10 LOW(0-5 good , 5-10 normal)
  // 10-35 ELEVATED
  //35-55 HIGH
  //55-125 VERY HIGH
  // > 125  EXTREMELY HIGH FOR STUDY PLACE...

    
  




  float customAQI = calculateStudyAQI(
      hum,
      eco2,
      tvoc,
      aqi,
      avgr
  );



//Dust (avgr)	35%	Particles and dust exposure
//eCO₂ (eco2)	25%	Ventilation indicator
//TVOC (tvoc)	20%	Gas and chemical pollution indicator
//ENS160 AQI (aqi)	15%	Additional sensor-derived air-quality indicator
//Humidity (hum)	5%	Indoor comfort and moisture



//90–100%	Excellent
//75–89%	Good
//60–74%	Moderate
//40–59%	Poor
//0–39%	Very poor

  String aqiLabel = getAQILabel(customAQI);




  // BUILD JSON PAYLOAD & QUEUE FOR BACKEND
  // =====================================================
  long rssi = WiFi.RSSI();
  String w_s;

  if (rssi >= -50) {
    w_s = "Excellent";
  } 
  else if (rssi >= -60) {
    w_s = "Very Good";
  } 
  else if (rssi >= -67) {
    w_s = "Good";
  } 
  else if (rssi >= -75) {
    w_s = "Fair";
  } 
  else if (rssi >= -85) {
    w_s = "Weak";
  } 
  else {
    w_s = "Very Weak";
  }
  float T = temp * 9.0 / 5.0 + 32.0;  // Celsius to Fahrenheit

  float HI = 0.5 * (T + 61.0 + ((T - 68.0) * 1.2)
                  + (hum * 0.094));

  if (HI >= 80.0) {
    HI = -42.379
         + 2.04901523 * T
         + 10.14333127 * hum
         - 0.22475541 * T * hum
         - 0.00683783 * T * T
         - 0.05481717 * hum * hum
         + 0.00122874 * T * T * hum
         + 0.00085282 * T * hum * hum
         - 0.00000199 * T * T * hum * hum;

    if (hum < 13.0 && T >= 80.0 && T <= 112.0) {
        HI -= ((13.0 - hum) / 4.0)
              * sqrt((17.0 - abs(T - 95.0)) / 17.0);
    } 
    else if (hum > 85.0 && T >= 80.0 && T <= 87.0) {
        HI += ((hum - 85.0) / 10.0)
              * ((87.0 - T) / 5.0);
    }
  }
  float feels_like = (HI - 32.0) * 5.0 / 9.0;
  // =====================================================
  // BUILD JSON PAYLOAD mewwa wenas keranne na na namai
  // =====================================================
  String jsonPayload = "{";
  jsonPayload += "\"area_id\": \"centra\","; 
  jsonPayload += "\"temp\":" + String(temp, 2) + ",";
  jsonPayload += "\"feels_like\":" + String(feels_like, 2) + ",";
  jsonPayload += "\"humidity\":" + String(hum, 2) + ",";
  jsonPayload += "\"custom_aqi_percent\":" + String(customAQI, 1) + ",";
  jsonPayload += "\"custom_aqi_label\":\"" + aqiLabel + "\",";
  jsonPayload += "\"eco2\":" + String(eco2, 2) + ",";
  jsonPayload += "\"tvoc\":" + String(tvoc, 2) + ",";
  jsonPayload += "\"ens_aqi\":" + String(int(aqi)) + ",";
  jsonPayload += "\"dust_ug_m3\":" + String(avgr, 2) + ",";
  jsonPayload += "\"noise_db\":" + String(fin, 2) + ",";
  jsonPayload += "\"wifi\":\"" + w_s + "\",";
  jsonPayload += "\"power\": true";
  jsonPayload += "}";

  // Send to Serial Monitor
  Serial.println(jsonPayload);

  // Create a heap-allocated pointer of the string
  String* payloadPtr = new String(jsonPayload);

  if (envDataQueue != nullptr) {
    // Send the pointer to the queue
    if (xQueueSend(envDataQueue, &payloadPtr, 0) != pdTRUE) {
      Serial.println("Warning: Queue full, dropping payload.");
      // CRITICAL: If the queue is full, delete the memory to prevent a leak
      delete payloadPtr; 
    }
  }

  // put your main code here, to run repeatedly:

}
