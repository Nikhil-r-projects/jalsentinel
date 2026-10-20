/*
 * ============================================================
 *  SENSOR NODE — "The Jellyfish" IoT Probe
 * ============================================================
 *  Reads environmental sensors (pH, TDS, Turbidity, Temperature)
 *  and sends data to the Flask AI server via HTTP POST.
 *
 *  Sensors:
 *  ┌────────────────┬──────────┬───────────────────────────┐
 *  │ Sensor         │ GPIO Pin │ Read Method               │
 *  ├────────────────┼──────────┼───────────────────────────┤
 *  │ pH Probe       │ GPIO 34  │ Analog (ADC1)             │
 *  │ TDS Probe      │ GPIO 35  │ Analog (ADC1)             │
 *  │ Turbidity      │ GPIO 32  │ Analog (ADC1)             │
 *  │ DHT11 (Temp)   │ GPIO 4   │ Digital (DHT library)     │
 *  └────────────────┴──────────┴───────────────────────────┘
 *
 *  Required Libraries (install via Arduino Library Manager):
 *  - WiFi (built-in with ESP32 board package)
 *  - HTTPClient (built-in with ESP32 board package)
 *  - DHT sensor library by Adafruit
 *  - Adafruit Unified Sensor (dependency of DHT lib)
 *
 *  Board: ESP32 Dev Module (or your specific ESP32 board)
 * ============================================================
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <DHT.h>
#include "esp_wifi.h"

// ─── WiFi Credentials ───────────────────────────────────────
// UPDATE THESE before uploading!
const char* WIFI_SSID     = "Nikhil ";
const char* WIFI_PASSWORD = "00000000";

// ─── Server Endpoint ────────────────────────────────────────
// Set this to your computer's local IP running server/app.py (found via 'ipconfig')
const char* SERVER_URL = "http://10.47.212.117:5000/sensor-data";

// ─── Sensor Pin Definitions ─────────────────────────────────
// Note: Use ADC1 pins only (GPIO 32-39) since WiFi uses ADC2
#define PH_PIN        34   // Analog pH sensor
#define TDS_PIN       35   // Analog TDS sensor
#define TURBIDITY_PIN 32   // Analog turbidity sensor
#define DHT_PIN        4   // Digital DHT11 sensor

// ─── DHT Sensor Setup ───────────────────────────────────────
#define DHTTYPE DHT11
DHT dht(DHT_PIN, DHTTYPE);

// ─── Calibration Constants ──────────────────────────────────
// Adjust these based on your specific sensor hardware
const float VREF          = 3.3;    // ESP32 ADC reference voltage
const float ADC_MAX       = 4095.0; // 12-bit ADC resolution

// pH Calibration: In neutral buffer (pH 7.0), module should output ~2.5V (adjusted via trimpot)
const float PH_V_NEUTRAL  = 2.50;   // Neutral reference voltage at pH 7.0
const float PH_SLOPE      = -3.5;   // Negative slope for inverting amplifier

// TDS Calibration: Enable INVERT_TDS_RESPONSE if your module drops voltage in contaminated water
const bool  INVERT_TDS_RESPONSE = true;  
const float TDS_FACTOR          = 0.5;   
const float TURB_FACTOR         = 0.1;   // Turbidity raw-to-NTU factor

// ─── Timing ─────────────────────────────────────────────────
const unsigned long SEND_INTERVAL = 5000; // Send data every 5 seconds
unsigned long lastSendTime = 0;

// ─── Read pH Sensor ─────────────────────────────────────────
float readPH() {
  long sum = 0;
  for (int i = 0; i < 20; i++) {
    sum += analogRead(PH_PIN);
    delay(5);
  }
  float avgRaw = sum / 20.0;
  float voltage = avgRaw * (VREF / ADC_MAX);
  
  // Standard inverting op-amp formula with neutral calibration offset
  float ph = 7.0 + ((PH_V_NEUTRAL - voltage) * PH_SLOPE);
  ph = constrain(ph, 0.0, 14.0);
  return ph;
}

// ─── Read TDS Sensor ────────────────────────────────────────
float readTDS(float temperature) {
  long sum = 0;
  for (int i = 0; i < 20; i++) {
    sum += analogRead(TDS_PIN);
    delay(5);
  }
  float avgRaw = sum / 20.0;
  float voltage = avgRaw * (VREF / ADC_MAX);

  // If the module outputs higher voltage for higher resistance (clean water):
  if (INVERT_TDS_RESPONSE) {
    voltage = VREF - voltage;
    if (voltage < 0) voltage = 0;
  }

  // Temperature compensation formula (standard Gravity TDS curve)
  float compCoeff = 1.0 + 0.02 * (temperature - 25.0);
  float compVolt  = voltage / compCoeff;

  // 3rd order polynomial curve for TDS in PPM
  float tds = (133.42 * compVolt * compVolt * compVolt 
             - 255.86 * compVolt * compVolt 
             + 857.39 * compVolt) * TDS_FACTOR;
  if (tds < 0) tds = 0;
  return tds;
}

// ─── Read Turbidity Sensor ──────────────────────────────────
float readTurbidity() {
  long sum = 0;
  for (int i = 0; i < 10; i++) {
    sum += analogRead(TURBIDITY_PIN);
    delay(10);
  }
  float avgRaw = sum / 10.0;
  float turbidity = avgRaw * TURB_FACTOR;
  return turbidity;
}

// ─── Read Temperature (DHT11) ───────────────────────────────
float readTemperature() {
  float temp = dht.readTemperature(); // Celsius
  if (isnan(temp)) {
    Serial.println("[Sensor] WARNING: DHT11 read failed, using fallback 25.0°C");
    return 25.0; // Safe fallback
  }
  return temp;
}

// ─── Setup ──────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(1000); // Give serial monitor time to connect

  Serial.println();
  Serial.println("============================================");
  Serial.println("  Jellyfish Sensor Node — Initializing...");
  Serial.println("============================================");

  // Initialize DHT sensor
  dht.begin();
  Serial.println("[Sensor] DHT11 initialized on GPIO 4");

  // Connect to WiFi
  WiFi.mode(WIFI_STA);
  esp_wifi_start();

  // Set country to India (enables channels 1-13)
  wifi_country_t country = {
    .cc = "IN",
    .schan = 1,
    .nchan = 13,
    .max_tx_power = 20,
    .policy = WIFI_COUNTRY_POLICY_AUTO
  };
  esp_wifi_set_country(&country);

  // Scan and list visible networks for diagnostics
  Serial.println("[Sensor] Scanning 2.4 GHz Wi-Fi channels...");
  int n = WiFi.scanNetworks();
  Serial.printf("[Sensor] Found %d networks:\n", n);
  for (int i = 0; i < n; i++) {
    Serial.printf("  [%d] SSID: '%s' | Ch: %d | RSSI: %d dBm\n", i, WiFi.SSID(i).c_str(), WiFi.channel(i), WiFi.RSSI(i));
  }

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("[Sensor] Connecting to WiFi");

  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 25) {
    delay(500);
    Serial.print(".");
    attempts++;
  }

  // If failed, try with trailing space (common on mobile hotspots)
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println();
    Serial.print("[Sensor] Retrying with SSID 'Nikhil ' (trailing space)...");
    WiFi.disconnect();
    delay(500);
    WiFi.begin("Nikhil ", WIFI_PASSWORD);
    attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 25) {
      delay(500);
      Serial.print(".");
      attempts++;
    }
  }

  if (WiFi.status() == WL_CONNECTED) {
    Serial.println();
    Serial.println("[Sensor] Connected to WiFi!");
    Serial.print("[Sensor] IP Address: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println();
    Serial.println("[Sensor] ERROR: WiFi connection failed!");
    Serial.println("[Sensor] NOTE: If hotspot uses WPA3-Personal, change hotspot Security to WPA2-Personal!");
    Serial.println("[Sensor] Will wait for connection...");
  }

  Serial.println("[Sensor] Server URL: " + String(SERVER_URL));
  Serial.println("[Sensor] Send interval: " + String(SEND_INTERVAL) + " ms");
  Serial.println("============================================");
  Serial.println("[Sensor] Ready! Sending telemetry...");
  Serial.println();
}

// ─── Main Loop ──────────────────────────────────────────────
void loop() {
  unsigned long currentTime = millis();

  // Non-blocking interval check
  if (currentTime - lastSendTime < SEND_INTERVAL) {
    return;
  }
  lastSendTime = currentTime;

  // Check WiFi connection
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[Sensor] Waiting for WiFi connection...");
    delay(3000);
    return;
  }

  // ─── Read all sensors ───────────────────────────────────
  float temperature = readTemperature();
  float ph          = readPH();
  float tds         = readTDS(temperature);
  float turbidity   = readTurbidity();

  // ─── Build JSON payload ─────────────────────────────────
  String payload = "{";
  payload += "\"node\":\"sensor_node\",";
  payload += "\"ph\":" + String(ph, 2) + ",";
  payload += "\"tds\":" + String(tds, 2) + ",";
  payload += "\"turbidity\":" + String(turbidity, 2) + ",";
  payload += "\"temperature\":" + String(temperature, 2);
  payload += "}";

  // ─── Send HTTP POST ─────────────────────────────────────
  HTTPClient http;
  http.begin(SERVER_URL);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(5000); // 5 second timeout

  int httpResponseCode = http.POST(payload);

  // ─── Log results ────────────────────────────────────────
  Serial.print("[Sensor] pH=");
  Serial.print(ph, 2);
  Serial.print(" TDS=");
  Serial.print(tds, 2);
  Serial.print(" Turb=");
  Serial.print(turbidity, 2);
  Serial.print(" Temp=");
  Serial.print(temperature, 2);

  if (httpResponseCode > 0) {
    Serial.print(" → Server: HTTP ");
    Serial.println(httpResponseCode);
  } else {
    Serial.print(" → ERROR: ");
    Serial.println(http.errorToString(httpResponseCode));
  }

  http.end();
}
