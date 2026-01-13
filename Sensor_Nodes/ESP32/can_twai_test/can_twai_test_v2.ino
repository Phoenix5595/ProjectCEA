// ESP32 TWAI + Dual MAX31865 (V2)
// - Uses native TWAI (CAN) on GPIO 5 (TX) / 4 (RX)
// - Adds a second MAX31865 on the other SPI bus
//   MAX1 on HSPI (SCK=14, MISO=12, MOSI=13, CS=2)
//   MAX2 on VSPI (SCK=18, MISO=19, MOSI=23, CS=27)
// - Sends two temperature frames: 0x101 (sensor 1) and 0x106 (sensor 2)

#include "driver/twai.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_err.h"
#include <SPI.h>
#include <math.h>

// MAX31865 registers
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
#define CONFIG_FAULT_DETECT 0x02
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

// CAN IDs
#define CAN_ID_PT100_TEMP1 0x101
#define CAN_ID_STATUS      0x102
#define CAN_ID_FAULTS      0x103
#define CAN_ID_CONFIG      0x104
#define CAN_ID_HEARTBEAT   0x105
#define CAN_ID_PT100_TEMP2 0x106

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

// State
bool can_initialized = false;
bool max1_ok = false;
bool max2_ok = false;
unsigned long message_count = 0;
String current_bitrate = "unknown";

// Recent temps
float last_temp1_c = -999.0f;
float last_temp2_c = -999.0f;
float max1_temperature = -999.0f;
float min1_temperature = 999.0f;
float max2_temperature = -999.0f;
float min2_temperature = 999.0f;

// TWAI configuration
twai_general_config_t g_config = TWAI_GENERAL_CONFIG_DEFAULT(CAN_TX_PIN, CAN_RX_PIN, TWAI_MODE_NORMAL);
twai_timing_config_t  t_config = TWAI_TIMING_CONFIG_250KBITS();
twai_filter_config_t  f_config = TWAI_FILTER_CONFIG_ACCEPT_ALL();

// SPI instances
SPIClass hspi(HSPI);
SPIClass vspi(VSPI);
SPISettings max_spi_settings(1000000, MSBFIRST, SPI_MODE1); // 1MHz, Mode1 per MAX31865

// MAX31865 helpers
void max_begin();
uint8_t max_read_reg(SPIClass &bus, int cs, uint8_t reg);
void max_write_reg(SPIClass &bus, int cs, uint8_t reg, uint8_t val);
void max_configure(SPIClass &bus, int cs);
float max_read_temperature_c(SPIClass &bus, int cs);

// CAN send helpers
void sendTempFrame(uint16_t can_id, float temperature_c, float max_c, float min_c);
void sendStatusFrame();
void sendHeartbeat();

// Math helpers
static inline int16_t to_fixed_100(float v) { return (int16_t)lroundf(v * 100.0f); }

void setup() {
  Serial.begin(115200);
  delay(1200);
  Serial.println("=== ESP32 Dual MAX31865 + TWAI (V2) ===");

  // Init SPI buses
  pinMode(MAX1_CS, OUTPUT); digitalWrite(MAX1_CS, HIGH);
  pinMode(MAX2_CS, OUTPUT); digitalWrite(MAX2_CS, HIGH);
  hspi.begin(MAX1_SCK, MAX1_MISO, MAX1_MOSI, MAX1_CS);
  vspi.begin(MAX2_SCK, MAX2_MISO, MAX2_MOSI, MAX2_CS);

  // Configure MAX31865s
  max_begin();

  // Init TWAI @ 250 kbps
  Serial.println("Init TWAI @ 250 kbps...");
  if (twai_driver_install(&g_config, &t_config, &f_config) == ESP_OK && twai_start() == ESP_OK) {
    can_initialized = true;
    current_bitrate = "250kbps";
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
  static unsigned long last10 = 0;
  if (millis() - last10 >= 10000) {
    // Read both sensors
    float t1 = max_read_temperature_c(hspi, MAX1_CS);
    float t2 = max_read_temperature_c(vspi, MAX2_CS);

    if (t1 != -999.0f) {
      last_temp1_c = t1;
      if (t1 > max1_temperature) max1_temperature = t1;
      if (t1 < min1_temperature) min1_temperature = t1;
    }
    if (t2 != -999.0f) {
      last_temp2_c = t2;
      if (t2 > max2_temperature) max2_temperature = t2;
      if (t2 < min2_temperature) min2_temperature = t2;
    }

    if (can_initialized) {
      if (last_temp1_c != -999.0f) {
        sendTempFrame(CAN_ID_PT100_TEMP1, last_temp1_c, max1_temperature, min1_temperature);
      }
      if (last_temp2_c != -999.0f) {
        sendTempFrame(CAN_ID_PT100_TEMP2, last_temp2_c, max2_temperature, min2_temperature);
      }
      sendStatusFrame();
      sendHeartbeat();
      message_count++;
    }

    Serial.print("[V2] T1="); Serial.print(last_temp1_c, 2);
    Serial.print(" C  T2="); Serial.print(last_temp2_c, 2);
    Serial.println(" C");

    last10 = millis();
  }
}

// ===== MAX31865 =====
void max_begin() {
  // Clear faults and configure both as 4-wire, bias on, auto convert, 50Hz
  max_write_reg(hspi, MAX1_CS, REG_CONFIG, CONFIG_CLEAR_FAULT);
  delay(5);
  max_write_reg(vspi, MAX2_CS, REG_CONFIG, CONFIG_CLEAR_FAULT);
  delay(5);

  uint8_t cfg = CONFIG_BIAS | CONFIG_AUTO_CONVERT | CONFIG_50HZ_FILTER; // 4-wire does not need CONFIG_3WIRE
  max_write_reg(hspi, MAX1_CS, REG_CONFIG, cfg);
  delay(5);
  max_write_reg(vspi, MAX2_CS, REG_CONFIG, cfg);
  delay(5);

  // Verify
  uint8_t r1 = max_read_reg(hspi, MAX1_CS, REG_CONFIG);
  uint8_t r2 = max_read_reg(vspi, MAX2_CS, REG_CONFIG);
  max1_ok = (r1 == cfg);
  max2_ok = (r2 == cfg);
  Serial.print("MAX1 cfg 0x"); Serial.print(r1, HEX); Serial.print(" => "); Serial.println(max1_ok ? "OK" : "FAIL");
  Serial.print("MAX2 cfg 0x"); Serial.print(r2, HEX); Serial.print(" => "); Serial.println(max2_ok ? "OK" : "FAIL");
}

uint8_t max_read_reg(SPIClass &bus, int cs, uint8_t reg) {
  uint8_t value = 0;
  bus.beginTransaction(max_spi_settings);
  digitalWrite(cs, LOW);
  bus.transfer(reg & 0x7F); // read: MSB=0
  value = bus.transfer(0x00);
  digitalWrite(cs, HIGH);
  bus.endTransaction();
  return value;
}

void max_write_reg(SPIClass &bus, int cs, uint8_t reg, uint8_t val) {
  bus.beginTransaction(max_spi_settings);
  digitalWrite(cs, LOW);
  bus.transfer(reg | 0x80); // write: MSB=1
  bus.transfer(val);
  digitalWrite(cs, HIGH);
  bus.endTransaction();
}

float max_read_temperature_c(SPIClass &bus, int cs) {
  // Read RTD
  uint8_t msb, lsb;
  bus.beginTransaction(max_spi_settings);
  digitalWrite(cs, LOW);
  bus.transfer(REG_RTD_MSB & 0x7F);
  msb = bus.transfer(0x00);
  lsb = bus.transfer(0x00);
  digitalWrite(cs, HIGH);
  bus.endTransaction();

  uint16_t rtd = ((uint16_t)msb << 8 | lsb) >> 1;
  if (rtd == 0) return -999.0f;

  float resistance = (rtd * RREF) / 32768.0f;
  float disc = A*A - 4*B*(1 - resistance/100.0f);
  if (disc < 0) return -999.0f;
  float temp = (-A + sqrtf(disc)) / (2*B);
  return temp;
}

// ===== CAN SEND =====
void sendTempFrame(uint16_t can_id, float temperature_c, float max_c, float min_c) {
  twai_message_t msg{};
  msg.identifier = can_id;
  msg.data_length_code = 8;
  msg.flags = TWAI_MSG_FLAG_NONE;

  int16_t t = to_fixed_100(temperature_c);
  int16_t mx = to_fixed_100(max_c);
  int16_t mn = to_fixed_100(min_c);

  msg.data[0] = (uint8_t)((t >> 8) & 0xFF);
  msg.data[1] = (uint8_t)(t & 0xFF);
  msg.data[2] = (uint8_t)((mx >> 8) & 0xFF);
  msg.data[3] = (uint8_t)(mx & 0xFF);
  msg.data[4] = (uint8_t)((mn >> 8) & 0xFF);
  msg.data[5] = (uint8_t)(mn & 0xFF);
  msg.data[6] = message_count & 0xFF;
  msg.data[7] = (message_count >> 8) & 0xFF;

  esp_err_t r = twai_transmit(&msg, pdMS_TO_TICKS(500));
  Serial.print("TX temp "); Serial.print(can_id, HEX); Serial.print(": "); Serial.println(esp_err_to_name(r));
}

void sendStatusFrame() {
  twai_message_t msg{};
  msg.identifier = CAN_ID_STATUS;
  msg.data_length_code = 8;
  msg.flags = TWAI_MSG_FLAG_NONE;

  msg.data[0] = 0x00; // status code ok
  msg.data[1] = max1_ok ? 0x01 : 0x00;
  msg.data[2] = can_initialized ? 0x01 : 0x00;
  int16_t last_fixed = to_fixed_100(last_temp1_c);
  msg.data[3] = (uint8_t)((last_fixed >> 8) & 0xFF);
  msg.data[4] = (uint8_t)(last_fixed & 0xFF);
  msg.data[5] = message_count & 0xFF;
  msg.data[6] = (message_count >> 8) & 0xFF;
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
  msg.data[2] = message_count & 0xFF;
  msg.data[3] = (message_count >> 8) & 0xFF;
  uint32_t up = millis();
  msg.data[4] = (uint8_t)(up >> 24);
  msg.data[5] = (uint8_t)(up >> 16);
  msg.data[6] = (uint8_t)(up >> 8);
  msg.data[7] = (uint8_t)(up);
  twai_transmit(&msg, pdMS_TO_TICKS(500));
}


