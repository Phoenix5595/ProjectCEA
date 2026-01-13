// ESP32 TWAI Full V4 (MAX31865 x2 + BME280 + SCD30 + VL53) - Multi-Node Support
// - Same as Full V3 but with updated CAN IDs for V3 receiver compatibility
// - Uses native TWAI (CAN) on GPIO 5 (TX) / 4 (RX)
// - MAX1 on HSPI (SCK=14, MISO=12, MOSI=13, CS=2)
// - MAX2 on VSPI (SCK=18, MISO=19, MOSI=23, CS=27)
// - I2C sensors on SDA=21, SCL=22
// - Supports 3 nodes with different CAN ID ranges

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

// CAN IDs based on node ID (V3 receiver compatible)
// Node 1: 0x101-0x106, Node 2: 0x201-0x206, Node 3: 0x301-0x306
#define CAN_ID_PT100       (0x100 + (NODE_ID * 0x100) + 0x01)  // 0x101, 0x201, 0x301 (Dry + Wet)
#define CAN_ID_BME280      (0x100 + (NODE_ID * 0x100) + 0x02)  // 0x102, 0x202, 0x302
#define CAN_ID_SCD30       (0x100 + (NODE_ID * 0x100) + 0x03)  // 0x103, 0x203, 0x303
#define CAN_ID_VL53        (0x100 + (NODE_ID * 0x100) + 0x04)  // 0x104, 0x204, 0x304
#define CAN_ID_STATUS      (0x100 + (NODE_ID * 0x100) + 0x05)  // 0x105, 0x205, 0x305
#define CAN_ID_HEARTBEAT   (0x100 + (NODE_ID * 0x100) + 0x06)  // 0x106, 0x206, 0x306

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
uint8_t max_read_reg(SPIClass &bus, int cs, uint8_t reg);
void max_write_reg(SPIClass &bus, int cs, uint8_t reg, uint8_t val);
void max_configure(SPIClass &bus, int cs);
float max_read_temperature_c(SPIClass &bus, int cs);

// CAN send helpers
void sendPT100Frame(float temp_dry_c, float temp_wet_c);
void sendStatusFrame();
void sendHeartbeat();
void sendBME280Frame();
void sendSCD30Frame();
void sendVL53Frame();

// Math helpers
static inline int16_t to_fixed_100(float v) { return (int16_t)lroundf(v * 100.0f); }
static inline uint16_t to_uint_fixed_100(float v) { long x = lroundf(v * 100.0f); if (x < 0) x = 0; if (x > 65535) x = 65535; return (uint16_t)x; }
static inline uint16_t to_uint_fixed_10(float v) { long x = lroundf(v * 10.0f); if (x < 0) x = 0; if (x > 65535) x = 65535; return (uint16_t)x; }

void setup() {
  Serial.begin(115200);
  delay(1200);
  Serial.printf("=== ESP32 FullV4 Node %d + TWAI ===\n", NODE_ID);

  // Init SPI buses
  pinMode(MAX1_CS, OUTPUT); digitalWrite(MAX1_CS, HIGH);
  pinMode(MAX2_CS, OUTPUT); digitalWrite(MAX2_CS, HIGH);
  hspi.begin(MAX1_SCK, MAX1_MISO, MAX1_MOSI, MAX1_CS);
  vspi.begin(MAX2_SCK, MAX2_MISO, MAX2_MOSI, MAX2_CS);

  // Configure MAX31865s
  max_begin();

  // Init I2C
  Wire.begin(I2C_SDA, I2C_SCL);

#if USE_BME280
  if (bme.begin(0x76) || bme.begin(0x77)) {
    bme_ok = true;
    Serial.println("BME280 OK");
  } else {
    Serial.println("BME280 not found");
  }
#endif

#if USE_SCD30
  scd_ok = scd30.begin(Wire);
  if (scd_ok) {
    scd30.setMeasurementInterval(2); // SCD30 updates ~2s; will repeat last reading on off seconds
    scd30.setAutoSelfCalibration(true);
    Serial.println("SCD30 OK");
  } else {
    Serial.println("SCD30 not found");
  }
#endif

#if USE_VL53
  vl_ok = vl53.begin();
  if (vl_ok) {
    Serial.println("VL53 OK");
  } else {
    Serial.println("VL53 not found");
  }
#endif

  // Init TWAI @ 250 kbps
  if (twai_driver_install(&g_config, &t_config, &f_config) == ESP_OK && twai_start() == ESP_OK) {
    can_initialized = true;
    twai_reconfigure_alerts(TWAI_ALERT_ALL, NULL);
    Serial.println("TWAI started.");
  } else {
    Serial.println("TWAI init failed.");
  }

  if (can_initialized) {
    sendStatusFrame();
    sendHeartbeat();
  }
}

void loop() {
  static unsigned long last1 = 0;
  if (millis() - last1 >= 1000) { // 1 second cadence
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
#if USE_SCD30
      if (scd_ok) {
        sendSCD30Frame();
      }
#endif
#if USE_VL53
      if (vl_ok) {
        sendVL53Frame();
      }
#endif

      sendStatusFrame();
      sendHeartbeat();
      message_count++;
    }

    Serial.printf("[Node%d] T1=%.2f°C T2=%.2f°C\n", NODE_ID, last_temp1_c, last_temp2_c);

    last1 = millis();
  }
}

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

void sendStatusFrame() {
  twai_message_t msg{};
  msg.identifier = CAN_ID_STATUS;
  msg.data_length_code = 8;
  msg.flags = TWAI_MSG_FLAG_NONE;

  // Compact flags: [max1_ok][max2_ok][bme_ok][scd_ok][vl53_ok][can_ok][pad][pad]
  msg.data[0] = max1_ok ? 0x01 : 0x00;
  msg.data[1] = max2_ok ? 0x01 : 0x00;
#if USE_BME280
  msg.data[2] = bme_ok ? 0x01 : 0x00;
#else
  msg.data[2] = 0x00;
#endif
#if USE_SCD30
  msg.data[3] = scd_ok ? 0x01 : 0x00;
#else
  msg.data[3] = 0x00;
#endif
#if USE_VL53
  msg.data[4] = vl_ok ? 0x01 : 0x00;
#else
  msg.data[4] = 0x00;
#endif
  msg.data[5] = can_initialized ? 0x01 : 0x00;
  msg.data[6] = 0x00;
  msg.data[7] = 0x00;

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
  if (scd30.dataAvailable()) {
    co2 = scd30.getCO2();
    t = scd30.getTemperature();
    rh = scd30.getHumidity();
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
