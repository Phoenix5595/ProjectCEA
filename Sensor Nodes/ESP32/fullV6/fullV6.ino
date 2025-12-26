// ESP32 TWAI Full V6 (MAX31865 x2 + BME280 + SCD30 + VL53) - Multi-Node Support
// - Same as Full V5 but with runtime sensor reconnection detection
// - Uses native TWAI (CAN) on GPIO 5 (TX) / 4 (RX)
// - MAX1 on HSPI (SCK=14, MISO=12, MOSI=13, CS=2)
// - MAX2 on VSPI (SCK=18, MISO=19, MOSI=23, CS=27)
// - I2C sensors on SDA=21, SCL=22
// - Supports 3 nodes with different CAN ID ranges
// - Automatically detects and reinitializes sensors when reconnected

#include "driver/twai.h"
#include "driver/gpio.h"
#include <SPI.h>
#include <Wire.h>
#include <math.h>

// ===== Node Configuration =====
// Set NODE_ID to 1, 2, or 3 for different CAN ID ranges
#ifndef NODE_ID
#define NODE_ID 1  // Change this to 1, 2, or 3 for different nodes
#endif

// ===== Compile-time enables (sensors required; default enabled) =====
#ifndef USE_BME280
#define USE_BME280 1
#endif
#ifndef USE_SCD30
#define USE_SCD30 1
#endif
#ifndef USE_VL53
#define USE_VL53 1
#endif

#if USE_BME280
#include <Adafruit_BME280.h>
#endif
#if USE_SCD30
#include <SparkFun_SCD30_Arduino_Library.h>
#endif
#if USE_VL53
#include <Adafruit_VL53L0X.h>
#endif

// ===== MAX31865 registers =====
#define REG_CONFIG 0x00
#define REG_RTD_MSB 0x01
#define REG_RTD_LSB 0x02
#define REG_HIGH_FAULT_MSB 0x03
#define REG_HIGH_FAULT_LSB 0x04
#define REG_LOW_FAULT_MSB 0x05
#define REG_LOW_FAULT_LSB 0x06
#define REG_FAULT_STATUS 0x07

// Configuration bits
#define CONFIG_BIAS 0x80
#define CONFIG_AUTO_CONVERT 0x40
#define CONFIG_1SHOT 0x20
#define CONFIG_3WIRE 0x10
#define CONFIG_CLEAR_FAULT 0x02
#define CONFIG_50HZ_FILTER 0x01

// PT100 coefficients
#define A 3.9083e-3
#define B -5.775e-7

// Reference resistor (Ohms)
#define RREF 430.0

// TWAI (CAN) pins
#define CAN_TX_PIN GPIO_NUM_5
#define CAN_RX_PIN GPIO_NUM_4

// CAN IDs based on node ID (V5 receiver compatible - no status frame)
// Node 1: 0x101-0x105, Node 2: 0x201-0x205, Node 3: 0x301-0x305
#define CAN_ID_PT100       (0x100 + ((NODE_ID - 1) * 0x100) + 0x01)  // 0x101, 0x201, 0x301 (Dry + Wet)
#define CAN_ID_BME280      (0x100 + ((NODE_ID - 1) * 0x100) + 0x02)  // 0x102, 0x202, 0x302
#define CAN_ID_SCD30       (0x100 + ((NODE_ID - 1) * 0x100) + 0x03)  // 0x103, 0x203, 0x303
#define CAN_ID_VL53        (0x100 + ((NODE_ID - 1) * 0x100) + 0x04)  // 0x104, 0x204, 0x304
#define CAN_ID_HEARTBEAT   (0x100 + ((NODE_ID - 1) * 0x100) + 0x05)  // 0x105, 0x205, 0x305

// SPI pins for MAX31865
// MAX1 on HSPI
#define MAX1_SCK 14
#define MAX1_MISO 12
#define MAX1_MOSI 13
#define MAX1_CS 2

// MAX2 on VSPI
#define MAX2_SCK 18
#define MAX2_MISO 19
#define MAX2_MOSI 23
#define MAX2_CS 27

// I2C pins
#define I2C_SDA 21
#define I2C_SCL 22

// State
bool can_initialized = false;
bool max1_ok = false;
bool max2_ok = false;
unsigned long message_count = 0;

// BME280 diagnostic info
uint8_t bme280_chip_id_76 = 0xFF;
uint8_t bme280_chip_id_77 = 0xFF;
bool bme280_found_at_76 = false;
bool bme280_found_at_77 = false;
uint8_t bme280_address = 0x76; // Remember which address worked

// Recent temps
float last_temp1_c = NAN;
float last_temp2_c = NAN;

// TWAI configuration
twai_general_config_t g_config = TWAI_GENERAL_CONFIG_DEFAULT(CAN_TX_PIN, CAN_RX_PIN, TWAI_MODE_NORMAL);
twai_timing_config_t  t_config = TWAI_TIMING_CONFIG_250KBITS();
twai_filter_config_t  f_config = TWAI_FILTER_CONFIG_ACCEPT_ALL();

// SPI instances
SPIClass hspi(HSPI);
SPIClass vspi(VSPI);
SPISettings max_spi_settings(1000000, MSBFIRST, SPI_MODE1); // 1MHz, Mode1 per MAX31865

// Optional sensors
#if USE_BME280
Adafruit_BME280 bme;
bool bme_ok = false;
#endif
#if USE_SCD30
SCD30 scd30;
bool scd_ok = false;
#endif
#if USE_VL53
Adafruit_VL53L0X vl53 = Adafruit_VL53L0X();
bool vl_ok = false;
#endif

// MAX31865 helpers
void max_begin();
void max_reinit_single(SPIClass &bus, int cs, bool &status_flag, const char* name);
uint8_t max_read_reg(SPIClass &bus, int cs, uint8_t reg);
void max_write_reg(SPIClass &bus, int cs, uint8_t reg, uint8_t val);
void max_configure(SPIClass &bus, int cs);
float max_read_temperature_c(SPIClass &bus, int cs);

// CAN send helpers
void sendPT100Frame(float temp_dry_c, float temp_wet_c);
void sendHeartbeat();
void sendBME280Frame();
void sendSCD30Frame();
void sendVL53Frame();

// I2C diagnostic helper
void scanI2C();
bool checkBME280ChipID(uint8_t addr);

// Sensor reconnection detection
void checkSensorReconnection();
bool tryReinitBME280();
bool tryReinitSCD30();
bool tryReinitVL53();
bool tryReinitMAX1();
bool tryReinitMAX2();

// Math helpers
static inline int16_t to_fixed_100(float v) { return (int16_t)lroundf(v * 100.0f); }
static inline uint16_t to_uint_fixed_100(float v) { long x = lroundf(v * 100.0f); if (x < 0) x = 0; if (x > 65535) x = 65535; return (uint16_t)x; }
static inline uint16_t to_uint_fixed_10(float v) { long x = lroundf(v * 10.0f); if (x < 0) x = 0; if (x > 65535) x = 65535; return (uint16_t)x; }

void setup() {
  Serial.begin(115200);
  delay(1200);
  Serial.println("\n\n========================================");
  Serial.printf("=== ESP32 FullV6 Node %d + TWAI ===\n", NODE_ID);
  Serial.println("=== With Sensor Reconnection Detection ===");
  Serial.println("========================================\n");

  // Init SPI buses
  pinMode(MAX1_CS, OUTPUT); digitalWrite(MAX1_CS, HIGH);
  pinMode(MAX2_CS, OUTPUT); digitalWrite(MAX2_CS, HIGH);
  hspi.begin(MAX1_SCK, MAX1_MISO, MAX1_MOSI, MAX1_CS);
  vspi.begin(MAX2_SCK, MAX2_MISO, MAX2_MOSI, MAX2_CS);

  // Configure MAX31865s
  max_begin();

  // Init I2C
  Wire.begin(I2C_SDA, I2C_SCL);
  delay(100); // Allow I2C bus to stabilize

  // Scan I2C bus for diagnostics
  Serial.println("\n--- I2C Bus Scan ---");
  scanI2C();
  Serial.println("--- End I2C Scan ---\n");

#if USE_BME280
  Serial.println("\n--- BME280 Initialization ---");
  Serial.println("Checking BME280 chip ID...");
  bool found_at_76 = checkBME280ChipID(0x76);
  bool found_at_77 = checkBME280ChipID(0x77);
  
  if (bme280_found_at_76) {
    Serial.println("BME280 chip ID found at 0x76, attempting initialization...");
    if (bme.begin(0x76, &Wire)) {
      bme_ok = true;
      bme280_address = 0x76;
      Serial.println("BME280 OK at address 0x76");
    } else {
      Serial.println("ERROR: BME280 chip detected but begin() failed at 0x76");
      Serial.println("Trying alternative initialization methods...");
      // Try with different settings
      Wire.setClock(100000); // Try slower I2C speed
      delay(10);
      if (bme.begin(0x76, &Wire)) {
        bme_ok = true;
        bme280_address = 0x76;
        Serial.println("BME280 OK at 0x76 (with slower I2C)");
      } else {
        Wire.setClock(400000); // Restore normal speed
        if (bme.begin(0x76)) {
          bme_ok = true;
          bme280_address = 0x76;
          Serial.println("BME280 OK at 0x76 (without explicit Wire)");
        }
      }
      Wire.setClock(400000); // Restore normal speed
    }
  } else if (bme280_found_at_77) {
    Serial.println("BME280 chip ID found at 0x77, attempting initialization...");
    if (bme.begin(0x77, &Wire)) {
      bme_ok = true;
      bme280_address = 0x77;
      Serial.println("BME280 OK at address 0x77");
    } else {
      Serial.println("ERROR: BME280 chip detected but begin() failed at 0x77");
      Serial.println("Trying alternative initialization methods...");
      Wire.setClock(100000);
      delay(10);
      if (bme.begin(0x77, &Wire)) {
        bme_ok = true;
        bme280_address = 0x77;
        Serial.println("BME280 OK at 0x77 (with slower I2C)");
      } else {
        Wire.setClock(400000);
        if (bme.begin(0x77)) {
          bme_ok = true;
          bme280_address = 0x77;
          Serial.println("BME280 OK at 0x77 (without explicit Wire)");
        }
      }
      Wire.setClock(400000);
    }
  } else {
    Serial.println("BME280 chip ID not found at 0x76 or 0x77");
    Serial.println("Attempting standard initialization anyway...");
    if (bme.begin(0x76, &Wire)) {
      bme_ok = true;
      bme280_address = 0x76;
      Serial.println("BME280 OK at 0x76 (standard init)");
    } else if (bme.begin(0x77, &Wire)) {
      bme_ok = true;
      bme280_address = 0x77;
      Serial.println("BME280 OK at 0x77 (standard init)");
    } else {
      Serial.println("BME280 initialization failed - check wiring and I2C address");
      Serial.println("Make sure SDO pin is connected to GND (0x76) or VCC (0x77)");
    }
  }
  
  if (!bme_ok) {
    Serial.println("WARNING: BME280 will not send data on CAN bus");
    Serial.println("Will periodically check for reconnection...");
  } else {
    Serial.println("BME280 initialization successful!");
  }
  Serial.println("--- End BME280 Init ---\n");
#endif

#if USE_SCD30
  scd_ok = scd30.begin(Wire);
  if (scd_ok) {
    scd30.setMeasurementInterval(2); // SCD30 updates ~2s; will repeat last reading on off seconds
    scd30.setAutoSelfCalibration(true);
    Serial.println("SCD30 OK");
  } else {
    Serial.println("SCD30 not found - will periodically check for reconnection...");
  }
#endif

#if USE_VL53
  vl_ok = vl53.begin();
  if (vl_ok) {
    Serial.println("VL53 OK");
  } else {
    Serial.println("VL53 not found - will periodically check for reconnection...");
  }
#endif

  // Init TWAI @ 250 kbps
  Serial.println("\n--- CAN Bus (TWAI) Initialization ---");
  if (twai_driver_install(&g_config, &t_config, &f_config) == ESP_OK && twai_start() == ESP_OK) {
    can_initialized = true;
    twai_reconfigure_alerts(TWAI_ALERT_ALL, NULL);
    Serial.println("TWAI started successfully.");
  } else {
    Serial.println("ERROR: TWAI init failed.");
  }
  Serial.println("--- End CAN Init ---\n");

  // Summary
  Serial.println("\n=== Initialization Summary ===");
  Serial.printf("MAX1: %s\n", max1_ok ? "OK" : "FAILED");
  Serial.printf("MAX2: %s\n", max2_ok ? "OK" : "FAILED");
#if USE_BME280
  Serial.printf("BME280: %s\n", bme_ok ? "OK" : "FAILED");
#endif
#if USE_SCD30
  Serial.printf("SCD30: %s\n", scd_ok ? "OK" : "FAILED");
#endif
#if USE_VL53
  Serial.printf("VL53: %s\n", vl_ok ? "OK" : "FAILED");
#endif
  Serial.printf("CAN (TWAI): %s\n", can_initialized ? "OK" : "FAILED");
  Serial.println("================================\n");
  Serial.println("Starting main loop with sensor reconnection detection...\n");

  if (can_initialized) {
    sendHeartbeat();
  }
}

void loop() {
  static unsigned long last1 = 0;
  static unsigned long last_heartbeat = 0;
  static unsigned long last_scd30 = 0;
  static unsigned long last_reconnect_check = 0;
  const unsigned long INTERVAL_MS = 1000;
  const unsigned long HEARTBEAT_INTERVAL_MS = 5000; // Heartbeat every 5 seconds
  const unsigned long SCD30_INTERVAL_MS = 5000; // SCD30 every 5 seconds (same as heartbeat)
  const unsigned long RECONNECT_CHECK_INTERVAL_MS = 10000; // Check for reconnections every 10 seconds
  
  unsigned long now = millis();
  
  // Check for sensor reconnections periodically
  if (now - last_reconnect_check >= RECONNECT_CHECK_INTERVAL_MS) {
    checkSensorReconnection();
    last_reconnect_check = now;
  }
  
  if (now - last1 >= INTERVAL_MS) {
    // Read both MAX sensors
    float t1 = max_read_temperature_c(hspi, MAX1_CS);
    float t2 = max_read_temperature_c(vspi, MAX2_CS);

    if (!isnan(t1)) {
      last_temp1_c = t1;
    }
    if (!isnan(t2)) {
      last_temp2_c = t2;
    }

    if (can_initialized) {
      // Send combined PT100 data if at least one sensor is working
      if ((max1_ok && !isnan(last_temp1_c)) || (max2_ok && !isnan(last_temp2_c))) {
        sendPT100Frame(last_temp1_c, last_temp2_c);
      }

      // Only send sensor data if sensor is detected and working
#if USE_BME280
      if (bme_ok) {
        sendBME280Frame();
      }
#endif
#if USE_VL53
      if (vl_ok) {
        sendVL53Frame();
      }
#endif

      message_count++;
    }

    Serial.printf("[Node%d] T1=%.2f°C T2=%.2f°C\n", NODE_ID, last_temp1_c, last_temp2_c);
    last1 = now;
  }
  
  // Send SCD30 every 5 seconds (same as heartbeat)
#if USE_SCD30
  if (can_initialized && scd_ok && (now - last_scd30 >= SCD30_INTERVAL_MS)) {
    sendSCD30Frame();
    last_scd30 = now;
  }
#endif
  
  // Send heartbeat less frequently
  if (can_initialized && (now - last_heartbeat >= HEARTBEAT_INTERVAL_MS)) {
    sendHeartbeat();
    last_heartbeat = now;
  }
  
  // Print status summary every 30 seconds (for debugging)
  static unsigned long last_status = 0;
  if (now - last_status >= 30000) {
    Serial.println("\n--- Status Summary ---");
    Serial.printf("MAX1: %s | MAX2: %s\n", max1_ok ? "OK" : "FAIL", max2_ok ? "OK" : "FAIL");
#if USE_BME280
    if (bme_ok) {
      Serial.println("BME280: OK");
    } else {
      Serial.println("BME280: FAIL");
      Serial.printf("  Chip ID at 0x76: 0x%02X", bme280_chip_id_76);
      if (bme280_chip_id_76 == 0x60) Serial.print(" (BME280 found!)");
      else if (bme280_chip_id_76 == 0x58) Serial.print(" (BMP280 - wrong chip)");
      else if (bme280_chip_id_76 == 0xFF) Serial.print(" (not found)");
      Serial.println();
      Serial.printf("  Chip ID at 0x77: 0x%02X", bme280_chip_id_77);
      if (bme280_chip_id_77 == 0x60) Serial.print(" (BME280 found!)");
      else if (bme280_chip_id_77 == 0x58) Serial.print(" (BMP280 - wrong chip)");
      else if (bme280_chip_id_77 == 0xFF) Serial.print(" (not found)");
      Serial.println();
      Serial.println("  Check: SDO pin connection (GND=0x76, VCC=0x77)");
    }
#endif
#if USE_SCD30
    Serial.printf("SCD30: %s\n", scd_ok ? "OK" : "FAIL");
#endif
#if USE_VL53
    Serial.printf("VL53: %s\n", vl_ok ? "OK" : "FAIL");
#endif
    Serial.printf("CAN: %s | Messages sent: %lu\n", can_initialized ? "OK" : "FAIL", message_count);
    Serial.println("----------------------\n");
    last_status = now;
  }
}

// ===== Sensor Reconnection Detection =====
void checkSensorReconnection() {
  // Only check sensors that are currently not working
  bool any_reconnected = false;
  
  // Check MAX1
  if (!max1_ok) {
    if (tryReinitMAX1()) {
      Serial.println("[RECONNECT] MAX1 sensor reconnected and reinitialized!");
      any_reconnected = true;
    }
  }
  
  // Check MAX2
  if (!max2_ok) {
    if (tryReinitMAX2()) {
      Serial.println("[RECONNECT] MAX2 sensor reconnected and reinitialized!");
      any_reconnected = true;
    }
  }
  
#if USE_BME280
  // Check BME280
  if (!bme_ok) {
    if (tryReinitBME280()) {
      Serial.println("[RECONNECT] BME280 sensor reconnected and reinitialized!");
      any_reconnected = true;
    }
  }
#endif

#if USE_SCD30
  // Check SCD30
  if (!scd_ok) {
    if (tryReinitSCD30()) {
      Serial.println("[RECONNECT] SCD30 sensor reconnected and reinitialized!");
      any_reconnected = true;
    }
  }
#endif

#if USE_VL53
  // Check VL53
  if (!vl_ok) {
    if (tryReinitVL53()) {
      Serial.println("[RECONNECT] VL53 sensor reconnected and reinitialized!");
      any_reconnected = true;
    }
  }
#endif

  if (any_reconnected) {
    Serial.println("Sensor reconnection check complete - some sensors were reconnected!");
  }
}

bool tryReinitMAX1() {
  // Try to read config register
  uint8_t cfg = CONFIG_BIAS | CONFIG_AUTO_CONVERT | CONFIG_50HZ_FILTER;
  
  // Clear faults first
  max_write_reg(hspi, MAX1_CS, REG_CONFIG, CONFIG_CLEAR_FAULT);
  delay(5);
  
  // Write config
  max_write_reg(hspi, MAX1_CS, REG_CONFIG, cfg);
  delay(5);
  
  // Read back and verify
  uint8_t r = max_read_reg(hspi, MAX1_CS, REG_CONFIG);
  if (r == cfg) {
    max1_ok = true;
    return true;
  }
  return false;
}

bool tryReinitMAX2() {
  // Try to read config register
  uint8_t cfg = CONFIG_BIAS | CONFIG_AUTO_CONVERT | CONFIG_50HZ_FILTER;
  
  // Clear faults first
  max_write_reg(vspi, MAX2_CS, REG_CONFIG, CONFIG_CLEAR_FAULT);
  delay(5);
  
  // Write config
  max_write_reg(vspi, MAX2_CS, REG_CONFIG, cfg);
  delay(5);
  
  // Read back and verify
  uint8_t r = max_read_reg(vspi, MAX2_CS, REG_CONFIG);
  if (r == cfg) {
    max2_ok = true;
    return true;
  }
  return false;
}

#if USE_BME280
bool tryReinitBME280() {
  // First check if chip ID is present at either address
  bool found_at_76 = checkBME280ChipID(0x76);
  bool found_at_77 = checkBME280ChipID(0x77);
  
  if (bme280_found_at_76) {
    // Try to initialize at 0x76
    if (bme.begin(0x76, &Wire)) {
      bme_ok = true;
      bme280_address = 0x76;
      return true;
    }
    // Try with slower I2C
    Wire.setClock(100000);
    delay(10);
    if (bme.begin(0x76, &Wire)) {
      bme_ok = true;
      bme280_address = 0x76;
      Wire.setClock(400000);
      return true;
    }
    Wire.setClock(400000);
  }
  
  if (bme280_found_at_77) {
    // Try to initialize at 0x77
    if (bme.begin(0x77, &Wire)) {
      bme_ok = true;
      bme280_address = 0x77;
      return true;
    }
    // Try with slower I2C
    Wire.setClock(100000);
    delay(10);
    if (bme.begin(0x77, &Wire)) {
      bme_ok = true;
      bme280_address = 0x77;
      Wire.setClock(400000);
      return true;
    }
    Wire.setClock(400000);
  }
  
  // If we previously had a working address, try that first
  if (bme280_address == 0x76 || bme280_address == 0x77) {
    if (bme.begin(bme280_address, &Wire)) {
      bme_ok = true;
      return true;
    }
  }
  
  return false;
}
#endif

#if USE_SCD30
bool tryReinitSCD30() {
  // Check if device responds at I2C address 0x61
  Wire.beginTransmission(0x61);
  uint8_t error = Wire.endTransmission();
  
  if (error == 0) {
    // Device is present, try to initialize
    if (scd30.begin(Wire)) {
      scd_ok = true;
      scd30.setMeasurementInterval(2);
      scd30.setAutoSelfCalibration(true);
      return true;
    }
  }
  
  return false;
}
#endif

#if USE_VL53
bool tryReinitVL53() {
  // Check if device responds at I2C address 0x29
  Wire.beginTransmission(0x29);
  uint8_t error = Wire.endTransmission();
  
  if (error == 0) {
    // Device is present, try to initialize
    if (vl53.begin()) {
      vl_ok = true;
      return true;
    }
  }
  
  return false;
}
#endif

// ===== MAX31865 =====
void max_begin() {
  // Clear faults and configure both as bias on, auto convert, 50Hz
  max_write_reg(hspi, MAX1_CS, REG_CONFIG, CONFIG_CLEAR_FAULT);
  delay(5);
  max_write_reg(vspi, MAX2_CS, REG_CONFIG, CONFIG_CLEAR_FAULT);
  delay(5);

  uint8_t cfg = CONFIG_BIAS | CONFIG_AUTO_CONVERT | CONFIG_50HZ_FILTER;
  max_write_reg(hspi, MAX1_CS, REG_CONFIG, cfg);
  delay(5);
  max_write_reg(vspi, MAX2_CS, REG_CONFIG, cfg);
  delay(5);

  uint8_t r1 = max_read_reg(hspi, MAX1_CS, REG_CONFIG);
  uint8_t r2 = max_read_reg(vspi, MAX2_CS, REG_CONFIG);
  max1_ok = (r1 == cfg);
  max2_ok = (r2 == cfg);
}

uint8_t max_read_reg(SPIClass &bus, int cs, uint8_t reg) {
  uint8_t value = 0;
  bus.beginTransaction(max_spi_settings);
  digitalWrite(cs, LOW);
  bus.transfer(reg & 0x7F);
  value = bus.transfer(0x00);
  digitalWrite(cs, HIGH);
  bus.endTransaction();
  return value;
}

void max_write_reg(SPIClass &bus, int cs, uint8_t reg, uint8_t val) {
  bus.beginTransaction(max_spi_settings);
  digitalWrite(cs, LOW);
  bus.transfer(reg | 0x80);
  bus.transfer(val);
  digitalWrite(cs, HIGH);
  bus.endTransaction();
}

float max_read_temperature_c(SPIClass &bus, int cs) {
  uint8_t msb, lsb;
  bus.beginTransaction(max_spi_settings);
  digitalWrite(cs, LOW);
  bus.transfer(REG_RTD_MSB & 0x7F);
  msb = bus.transfer(0x00);
  lsb = bus.transfer(0x00);
  digitalWrite(cs, HIGH);
  bus.endTransaction();

  uint16_t rtd = ((uint16_t)msb << 8 | lsb) >> 1;
  if (rtd == 0) return NAN;

  float resistance = (rtd * RREF) / 32768.0f;
  float disc = A*A - 4*B*(1.0f - resistance/100.0f);
  if (disc < 0) return NAN;
  float temp = (-A + sqrtf(disc)) / (2*B);
  return temp;
}

// ===== CAN SEND =====
void sendPT100Frame(float temp_dry_c, float temp_wet_c) {
  twai_message_t msg{};
  msg.identifier = CAN_ID_PT100;
  msg.data_length_code = 8;
  msg.flags = TWAI_MSG_FLAG_NONE;

  // Convert temperatures to fixed point (x100)
  int16_t t_dry = isnan(temp_dry_c) ? 0x7FFF : to_fixed_100(temp_dry_c);  // 0x7FFF = invalid
  int16_t t_wet = isnan(temp_wet_c) ? 0x7FFF : to_fixed_100(temp_wet_c);  // 0x7FFF = invalid

  msg.data[0] = (uint8_t)((t_dry >> 8) & 0xFF);
  msg.data[1] = (uint8_t)(t_dry & 0xFF);
  msg.data[2] = (uint8_t)((t_wet >> 8) & 0xFF);
  msg.data[3] = (uint8_t)(t_wet & 0xFF);
  msg.data[4] = message_count & 0xFF;
  msg.data[5] = (message_count >> 8) & 0xFF;
  msg.data[6] = 0x00;  // Reserved
  msg.data[7] = 0x00;  // Reserved

  twai_transmit(&msg, pdMS_TO_TICKS(500));
}

void sendHeartbeat() {
  twai_message_t msg{};
  msg.identifier = CAN_ID_HEARTBEAT;
  msg.data_length_code = 8;
  msg.flags = TWAI_MSG_FLAG_NONE;
  msg.data[0] = 0xAA;
  msg.data[1] = 0x55;
  uint32_t up = millis();
  msg.data[2] = (uint8_t)(up >> 24);
  msg.data[3] = (uint8_t)(up >> 16);
  msg.data[4] = (uint8_t)(up >> 8);
  msg.data[5] = (uint8_t)(up);
  msg.data[6] = 0x00;
  msg.data[7] = 0x00;
  twai_transmit(&msg, pdMS_TO_TICKS(500));
}

void sendBME280Frame() {
#if USE_BME280
  // Only send if sensor is detected and working
  if (!bme_ok) return;
  
  float t = bme.readTemperature();      // °C
  float rh = bme.readHumidity();        // %
  float p = bme.readPressure() / 100.0; // hPa

  twai_message_t msg{};
  msg.identifier = CAN_ID_BME280;
  msg.data_length_code = 8;
  msg.flags = TWAI_MSG_FLAG_NONE;

  int16_t t_fixed = to_fixed_100(t);
  uint16_t rh_fixed = to_uint_fixed_100(rh);
  uint16_t p_fixed = to_uint_fixed_10(p);

  msg.data[0] = (uint8_t)((t_fixed >> 8) & 0xFF);
  msg.data[1] = (uint8_t)(t_fixed & 0xFF);
  msg.data[2] = (uint8_t)((rh_fixed >> 8) & 0xFF);
  msg.data[3] = (uint8_t)(rh_fixed & 0xFF);
  msg.data[4] = (uint8_t)((p_fixed >> 8) & 0xFF);
  msg.data[5] = (uint8_t)(p_fixed & 0xFF);
  msg.data[6] = 0x00;
  msg.data[7] = 0x00;
  twai_transmit(&msg, pdMS_TO_TICKS(500));
#endif
}

void sendSCD30Frame() {
#if USE_SCD30
  // Only send if sensor is detected and working
  if (!scd_ok) return;
  
  float co2 = 400.0f;  // Default fallback
  float t = 0.0f;
  float rh = 0.0f;
  
  // Wait a bit and try reading with retry to ensure data is available
  // SCD30 updates every 2s, but we read every 5s so data should be ready
  delay(100); // Small delay to ensure I2C is ready
  for (int retry = 0; retry < 5; retry++) {
    if (scd30.dataAvailable()) {
      co2 = scd30.getCO2();
      t = scd30.getTemperature();
      rh = scd30.getHumidity();
      // Validate readings before using them
      if (co2 > 0 && co2 < 10000 && !isnan(t) && !isnan(rh) && rh >= 0 && rh <= 100) {
        break; // Got valid data, exit retry loop
      }
    }
    if (retry < 4) delay(100); // Delay before retry
  }

  uint16_t co2_u16 = (co2 < 0) ? 0 : (co2 > 65535 ? 65535 : (uint16_t)lroundf(co2));
  int16_t t_fixed = to_fixed_100(t);
  uint16_t rh_fixed = to_uint_fixed_100(rh);

  twai_message_t msg{};
  msg.identifier = CAN_ID_SCD30;
  msg.data_length_code = 8;
  msg.flags = TWAI_MSG_FLAG_NONE;

  msg.data[0] = (uint8_t)((co2_u16 >> 8) & 0xFF);
  msg.data[1] = (uint8_t)(co2_u16 & 0xFF);
  msg.data[2] = (uint8_t)((t_fixed >> 8) & 0xFF);
  msg.data[3] = (uint8_t)(t_fixed & 0xFF);
  msg.data[4] = (uint8_t)((rh_fixed >> 8) & 0xFF);
  msg.data[5] = (uint8_t)(rh_fixed & 0xFF);
  msg.data[6] = 0x00;
  msg.data[7] = 0x00;
  twai_transmit(&msg, pdMS_TO_TICKS(500));
#endif
}

void sendVL53Frame() {
#if USE_VL53
  // Only send if sensor is detected and working
  if (!vl_ok) return;
  
  uint16_t dist = 0;
  uint16_t amb = 0;
  uint16_t sig = 0;
  
  VL53L0X_RangingMeasurementData_t measure;
  vl53.rangingTest(&measure, false);
  if (measure.RangeStatus == 0) {
    dist = (measure.RangeMilliMeter > 65535) ? 65535 : (uint16_t)measure.RangeMilliMeter;
  }
  // Ambient/signal values are not exposed in Adafruit driver; keep zeros by default.

  twai_message_t msg{};
  msg.identifier = CAN_ID_VL53;
  msg.data_length_code = 8;
  msg.flags = TWAI_MSG_FLAG_NONE;
  msg.data[0] = (uint8_t)((dist >> 8) & 0xFF);
  msg.data[1] = (uint8_t)(dist & 0xFF);
  msg.data[2] = (uint8_t)((amb >> 8) & 0xFF);
  msg.data[3] = (uint8_t)(amb & 0xFF);
  msg.data[4] = (uint8_t)((sig >> 8) & 0xFF);
  msg.data[5] = (uint8_t)(sig & 0xFF);
  msg.data[6] = 0x00;
  msg.data[7] = 0x00;
  twai_transmit(&msg, pdMS_TO_TICKS(500));
#endif
}

// ===== I2C Scanner =====
void scanI2C() {
  byte error, address;
  int nDevices = 0;

  Serial.println("Scanning I2C bus...");
  for (address = 1; address < 127; address++) {
    Wire.beginTransmission(address);
    error = Wire.endTransmission();

    if (error == 0) {
      Serial.printf("I2C device found at address 0x%02X", address);
      // Try to identify common sensors
      if (address == 0x61) {
        Serial.print(" (likely SCD30)");
      } else if (address == 0x29) {
        Serial.print(" (likely VL53L0X)");
      } else if (address == 0x76 || address == 0x77) {
        Serial.print(" (possible BME280)");
      }
      Serial.println();
      nDevices++;
    } else if (error == 4) {
      Serial.printf("Unknown error at address 0x%02X\n", address);
    }
  }
  
  if (nDevices == 0) {
    Serial.println("No I2C devices found - check wiring!");
  } else {
    Serial.printf("Found %d device(s)\n", nDevices);
  }
}

// ===== BME280 Chip ID Check =====
bool checkBME280ChipID(uint8_t addr) {
  Wire.beginTransmission(addr);
  Wire.write(0xD0); // Chip ID register
  uint8_t error = Wire.endTransmission();
  
  if (error != 0) {
    Serial.printf("  Address 0x%02X: I2C error %d\n", addr, error);
    if (addr == 0x76) bme280_chip_id_76 = 0xFF;
    if (addr == 0x77) bme280_chip_id_77 = 0xFF;
    return false;
  }
  
  if (Wire.requestFrom(addr, (uint8_t)1) != 1) {
    Serial.printf("  Address 0x%02X: Failed to read chip ID\n", addr);
    if (addr == 0x76) bme280_chip_id_76 = 0xFF;
    if (addr == 0x77) bme280_chip_id_77 = 0xFF;
    return false;
  }
  
  uint8_t chipId = Wire.read();
  if (addr == 0x76) bme280_chip_id_76 = chipId;
  if (addr == 0x77) bme280_chip_id_77 = chipId;
  
  Serial.printf("  Address 0x%02X: Chip ID = 0x%02X", addr, chipId);
  
  if (chipId == 0x60) {
    Serial.println(" (BME280 confirmed!)");
    if (addr == 0x76) bme280_found_at_76 = true;
    if (addr == 0x77) bme280_found_at_77 = true;
    return true;
  } else if (chipId == 0x58) {
    Serial.println(" (BMP280 - no humidity sensor)");
    return false;
  } else if (chipId == 0x56 || chipId == 0x57) {
    Serial.println(" (BMP180)");
    return false;
  } else if (chipId == 0xFF || chipId == 0x00) {
    Serial.println(" (no device or communication error)");
    return false;
  } else {
    Serial.printf(" (unknown chip - might not be BME280)\n");
    return false;
  }
}

