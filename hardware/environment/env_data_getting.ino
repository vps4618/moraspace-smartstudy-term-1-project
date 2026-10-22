#include <Wire.h>
#include <Adafruit_AHTX0.h>
#include "ScioSense_ENS160.h"
#include <Arduino.h>
#include "driver/i2s.h"
#include "driver/adc.h"
#include <WiFi.h>
#include <ESPmDNS.h>

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

#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include "secrets.h" // Your file with WIFI_SSID, WIFI_PASSWORD, and SECRET_API_KEY

const char* serverUrl = "https://smartstudy-backend-psi.vercel.app/api/env/sync";

// FreeRTOS queue to hold JSON strings waiting to be sent
QueueHandle_t envDataQueue = nullptr;

Adafruit_AHTX0 aht;       //ens+bme

ScioSense_ENS160 ens160(ENS160_I2CADDR_1);  // 0x53


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

  server.begin();

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



  // 0-10 LOW(0-5 good , 5-10 normal)
  // 10-35 ELEVATED
  //35-55 HIGH
  //55-125 VERY HIGH
  // > 125  EXTREMELY HIGH FOR STUDY PLACE...



  // =====================================================
  // BUILD JSON PAYLOAD & QUEUE FOR BACKEND
  // =====================================================
  long rssi = WiFi.RSSI();
  bool is_wifi_good = (rssi > -85);
  float feels_like = temp + (hum * 0.05); // Rough heat index calculation

  // Construct JSON to match FastAPI EnvTelemetry schema
  String jsonPayload = "{";
  jsonPayload += "\"area_id\": \"apaya\","; 
  jsonPayload += "\"aqi\":" + String(int(aqi)) + ",";
  jsonPayload += "\"temp\":" + String(temp, 2) + ",";
  jsonPayload += "\"feels_like\":" + String(feels_like, 2) + ",";
  jsonPayload += "\"humidity\":" + String(hum, 2) + ",";
  jsonPayload += "\"noise\":" + String(fin, 2) + ",";
  jsonPayload += "\"wifi\":" + String(is_wifi_good ? "true" : "false") + ",";
  jsonPayload += "\"power\": true";
  jsonPayload += "}";

  Serial.println("Queuing data: " + jsonPayload);

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
}