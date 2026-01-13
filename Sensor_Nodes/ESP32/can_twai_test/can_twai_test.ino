#include "driver/twai.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_err.h"
#include <math.h>

// Software SPI implementation for MAX31865
// MAX31865 Registers
#define REG_CONFIG 0x00
#define REG_RTD_MSB 0x01
#define REG_RTD_LSB 0x02
#define REG_HIGH_FAULT_MSB 0x03
#define REG_HIGH_FAULT_LSB 0x04
#define REG_LOW_FAULT_MSB 0x05
#define REG_LOW_FAULT_LSB 0x06
#define REG_FAULT_STATUS 0x07

// Configuration values
#define CONFIG_BIAS 0x80
#define CONFIG_AUTO_CONVERT 0x40
#define CONFIG_1SHOT 0x20
#define CONFIG_3WIRE 0x10
#define CONFIG_FAULT_DETECT 0x02
#define CONFIG_CLEAR_FAULT 0x02
#define CONFIG_50HZ_FILTER 0x01

// Pin definitions for Software SPI (use any pins you want)
#define MAX1_CS 2   // CS pin (D2) - any available pin
#define MAX1_MOSI 13 // MOSI pin (D13) - any available pin
#define MAX1_MISO 12 // MISO pin (D12) - any available pin
#define MAX1_SCK 14  // SCK pin (D14) - any available pin

// TWAI (CAN) pin definitions
#define CAN_TX_PIN GPIO_NUM_5   // TX pin for TWAI
#define CAN_RX_PIN GPIO_NUM_4   // RX pin for TWAI

// V2: control whether to send diagnostic PING (0x120, BE EF) on the bus
// 0 = serial-only heartbeat; 1 = send on-bus PING every 1s
#define TWAI_SEND_PING_V2 0

// CAN Message IDs
#define CAN_ID_PT100_TEMP 0x101    // Temperature data
#define CAN_ID_STATUS 0x102        // Status and health
#define CAN_ID_FAULTS 0x103        // Fault information
#define CAN_ID_CONFIG 0x104        // Configuration data
#define CAN_ID_HEARTBEAT 0x105     // Heartbeat message

// Reference resistor value (Ohms) - for 430Ω board (Adafruit)
#define RREF 430.0

// PT100 Callendar-Van Dusen coefficients
#define A 3.9083e-3
#define B -5.775e-7

// Status variables
bool can_initialized = false;
bool max31865_working = false;
unsigned long message_count = 0;
unsigned long last_can_send = 0;
float max_temperature = -999.0;
float min_temperature = 999.0;
float last_valid_temp = -999.0;
String current_bitrate = "unknown";
bool twai_recovering = false;

// Message tracking
unsigned long messages_received = 0;
unsigned long last_received_id = 0;
unsigned long last_received_time = 0;
String last_received_data = "";

// TWAI configuration
twai_general_config_t g_config = TWAI_GENERAL_CONFIG_DEFAULT(CAN_TX_PIN, CAN_RX_PIN, TWAI_MODE_NORMAL);
twai_timing_config_t t_config = TWAI_TIMING_CONFIG_250KBITS();
twai_filter_config_t f_config = TWAI_FILTER_CONFIG_ACCEPT_ALL();

void setup() {
  Serial.begin(115200);
  delay(2000);  // Wait for Serial
  
  Serial.println("=== ESP32 MAX31865 + TWAI CAN Bus ===");
  Serial.println("Using native ESP32 TWAI controller...");
  
  Serial.println("Step 1: Starting...");
  
  // Initialize Software SPI pins
  pinMode(MAX1_CS, OUTPUT);
  pinMode(MAX1_MOSI, OUTPUT);
  pinMode(MAX1_MISO, INPUT);
  pinMode(MAX1_SCK, OUTPUT);
  
  Serial.println("Step 2: SPI pins initialized");
  
  digitalWrite(MAX1_CS, HIGH);
  digitalWrite(MAX1_MOSI, LOW);
  digitalWrite(MAX1_SCK, LOW);
  
  Serial.println("Step 3: SPI pin states set");
  
  // Initialize CS pin
  pinMode(MAX1_CS, OUTPUT);
  digitalWrite(MAX1_CS, HIGH);
  Serial.println("Step 4: CS pin initialized");
  
  // Initialize TWAI (CAN) Bus at fixed 250 kbps
  Serial.println("Step 5: Initializing TWAI CAN Bus (fixed 250kbps)...");
  bool can_init_success = false;
  if (twai_driver_install(&g_config, &t_config, &f_config) == ESP_OK) {
    Serial.println("TWAI driver installed successfully");
    if (twai_start() == ESP_OK) {
      can_init_success = true;
      can_initialized = true;
      current_bitrate = "250kbps";
    } else {
      Serial.println("Failed to start TWAI driver");
      twai_driver_uninstall();
    }
  } else {
    Serial.println("Failed to install TWAI driver");
  }

  if (can_init_success) {
    Serial.println("Step 6: TWAI CAN BUS init ok!");
    Serial.print("FINAL BITRATE: ");
    Serial.println(current_bitrate);
    // Enable all TWAI alerts for diagnostics
    esp_err_t alert_cfg_res = twai_reconfigure_alerts(TWAI_ALERT_ALL, NULL);
    Serial.print("Alerts enable result: ");
    Serial.println(esp_err_to_name(alert_cfg_res));
    
    // Test CAN transmission immediately
    Serial.println("Step 6.1: Testing CAN transmission...");
    twai_message_t test_msg;
    test_msg.identifier = 0x123;
    test_msg.data_length_code = 8;
    test_msg.flags = TWAI_MSG_FLAG_NONE;
    test_msg.data[0] = 0xAA;
    test_msg.data[1] = 0xBB;
    test_msg.data[2] = 0xCC;
    test_msg.data[3] = 0xDD;
    test_msg.data[4] = 0xEE;
    test_msg.data[5] = 0xFF;
    test_msg.data[6] = 0x00;
    test_msg.data[7] = 0x11;
    
    if (twai_transmit(&test_msg, pdMS_TO_TICKS(1000)) == ESP_OK) {
      Serial.println("OK Test message sent successfully!");
    } else {
      Serial.println("FAIL Test message send failed!");
    }
    
    // Send initial status message
    sendStatusMessage("ESP32 Starting", 0x01);
  } else {
    Serial.println("Step 6: TWAI CAN BUS init failed on all bitrates!");
    can_initialized = false;
  }
  
  // Test basic pin states
  Serial.println("Step 7: Testing pin states...");
  Serial.print("CS pin (D2): ");
  Serial.println(digitalRead(MAX1_CS));
  Serial.print("MOSI pin (D13): ");
  Serial.println(digitalRead(MAX1_MOSI));
  Serial.print("MISO pin (D12): ");
  Serial.println(digitalRead(MAX1_MISO));
  Serial.print("SCK pin (D14): ");
  Serial.println(digitalRead(MAX1_SCK));
  
  // Test CS pin
  Serial.println("Step 8: Testing CS pin manually...");
  digitalWrite(MAX1_CS, LOW);
  delay(100);
  digitalWrite(MAX1_CS, HIGH);
  delay(100);
  
  // Read config register multiple times
  Serial.println("Step 9: Reading config register multiple times...");
  for (int i = 0; i < 5; i++) {
    uint8_t config = readRegister(REG_CONFIG);
    Serial.print("Config register attempt ");
    Serial.print(i + 1);
    Serial.print(": 0x");
    Serial.println(config, HEX);
    delay(100);
  }
  
  // Read fault status
  Serial.println("Step 10: Reading fault status...");
  uint8_t fault = readRegister(REG_FAULT_STATUS);
  Serial.print("Fault status: 0x");
  Serial.println(fault, HEX);
  
  // Read RTD registers
  Serial.println("Step 11: Reading RTD registers...");
  uint8_t rtd_msb = readRegister(REG_RTD_MSB);
  uint8_t rtd_lsb = readRegister(REG_RTD_LSB);
  Serial.print("RTD MSB: 0x");
  Serial.print(rtd_msb, HEX);
  Serial.print(", LSB: 0x");
  Serial.println(rtd_lsb, HEX);
  
  // Test writing to config register
  Serial.println("\nStep 12: Testing Write...");
  Serial.println("=== Testing Write ===");
  writeRegister(REG_CONFIG, 0x81);  // Test value
  delay(100);
  uint8_t new_config = readRegister(REG_CONFIG);
  Serial.print("Wrote 0x81, read back: 0x");
  Serial.println(new_config, HEX);
  
  if (new_config == 0x81) {
    Serial.println("OK SPI communication working!");
    max31865_working = true;
  } else {
    Serial.println("FAIL SPI communication failed!");
    max31865_working = false;
  }
  
  // Configure MAX31865 for PT100 operation
  Serial.println("\nStep 13: Configuring MAX31865...");
  Serial.println("=== Configuring MAX31865 ===");
  configureMAX31865();
  
  Serial.println("Step 14: Setup complete!");
  
  // Send configuration message
  if (can_initialized) {
    sendConfigMessage();
    sendHeartbeat();
  }
}

void loop() {
  // Check for incoming CAN messages
  // Read TWAI alerts (non-blocking) and handle BUS-OFF recovery
  uint32_t alerts = 0;
  if (twai_read_alerts(&alerts, 0) == ESP_OK && alerts != 0) {
    Serial.print("TWAI Alerts: 0x");
    Serial.println(alerts, HEX);
    if (alerts & TWAI_ALERT_BUS_OFF) {
      Serial.println("TWAI ALERT: BUS_OFF detected -> initiating recovery");
      if (!twai_recovering) {
        if (twai_initiate_recovery() == ESP_OK) {
          twai_recovering = true;
          Serial.println("Recovery started; waiting for BUS_RECOVERED alert");
        } else {
          Serial.println("Failed to initiate recovery");
        }
      }
    }
    if (alerts & TWAI_ALERT_BUS_RECOVERED) {
      Serial.println("TWAI ALERT: BUS_RECOVERED -> restarting TWAI");
      if (twai_start() == ESP_OK) {
        twai_recovering = false;
        Serial.println("TWAI restarted after recovery");
      } else {
        Serial.println("Failed to restart TWAI after recovery");
      }
    }
    if (alerts & TWAI_ALERT_ERR_PASS) Serial.println("TWAI ALERT: ERROR_PASSIVE");
    if (alerts & TWAI_ALERT_ERR_ACTIVE) Serial.println("TWAI ALERT: ERROR_ACTIVE");
    if (alerts & TWAI_ALERT_BUS_ERROR) Serial.println("TWAI ALERT: BUS_ERROR");
    if (alerts & TWAI_ALERT_RX_QUEUE_FULL) Serial.println("TWAI ALERT: RX_QUEUE_FULL");
    if (alerts & TWAI_ALERT_TX_FAILED) Serial.println("TWAI ALERT: TX_FAILED");
    if (alerts & TWAI_ALERT_ARB_LOST) Serial.println("TWAI ALERT: ARB_LOST");
  }
  
  // Periodic heartbeat every 1s (V2): by default, serial-only
  if ((millis() - last_can_send) >= 1000) {
#if TWAI_SEND_PING_V2
    if (can_initialized) {
      twai_message_t ping;
      ping.identifier = 0x120;
      ping.data_length_code = 2;
      ping.flags = TWAI_MSG_FLAG_NONE;
      ping.data[0] = 0xBE;
      ping.data[1] = 0xEF;
      esp_err_t r = twai_transmit(&ping, pdMS_TO_TICKS(100));
      Serial.print("PING send: ");
      Serial.println(esp_err_to_name(r));
    }
#else
    Serial.println("PING (serial only; TWAI_SEND_PING_V2=0)");
#endif
    last_can_send = millis();
  }
  twai_message_t rx_msg;
  if (twai_receive(&rx_msg, pdMS_TO_TICKS(10)) == ESP_OK) {
    Serial.println("\n=== RECEIVED CAN MESSAGE ===");
    
    // Track the message
    messages_received++;
    last_received_id = rx_msg.identifier;
    last_received_time = millis();
    last_received_data = "";
    for(int i = 0; i < rx_msg.data_length_code; i++) {
      if (rx_msg.data[i] < 0x10) last_received_data += "0";
      last_received_data += String(rx_msg.data[i], HEX);
      if (i < rx_msg.data_length_code-1) last_received_data += " ";
    }
    
    Serial.print("Received ID: 0x");
    Serial.print(rx_msg.identifier, HEX);
    Serial.print(" Length: ");
    Serial.print(rx_msg.data_length_code);
    Serial.print(" Data: ");
    Serial.println(last_received_data);
    Serial.println("=== END RECEIVED MESSAGE ===\n");
  }
  
  // Read all registers and temperature every 10 seconds
  static unsigned long lastRead = 0;
  if (millis() - lastRead >= 10000) {
    Serial.println("\n=== Register Dump & Temperature ===");
    // TWAI status snapshot
    if (can_initialized) {
      twai_status_info_t st;
      if (twai_get_status_info(&st) == ESP_OK) {
        Serial.println("--- TWAI Status ---");
        Serial.print("State: ");
        Serial.println((int)st.state);
        Serial.print("TX err: "); Serial.print((int)st.tx_error_counter);
        Serial.print("  RX err: "); Serial.println((int)st.rx_error_counter);
        Serial.print("Msgs to TX: "); Serial.print((int)st.msgs_to_tx);
        Serial.print("  Msgs to RX: "); Serial.println((int)st.msgs_to_rx);
        Serial.print("TX failed: "); Serial.print((int)st.tx_failed_count);
        Serial.print("  RX missed: "); Serial.println((int)st.rx_missed_count);
      }
    }
    
    // Read all registers
    for (uint8_t reg = 0x00; reg <= 0x07; reg++) {
      uint8_t value = readRegister(reg);
      Serial.print("Reg 0x");
      Serial.print(reg, HEX);
      Serial.print(": 0x");
      Serial.println(value, HEX);
    }
    
    // Read temperature
    float temp = readTemperature();
    Serial.print("Temperature: ");
    Serial.print(temp);
    Serial.println(" C");
    
    // Update temperature tracking
    if (temp != -999.0) {
      last_valid_temp = temp;
      if (temp > max_temperature) max_temperature = temp;
      if (temp < min_temperature) min_temperature = temp;
    }
    
    // Send enhanced CAN messages
    if (can_initialized) {
      // Send temperature data
      if (temp != -999.0) {
        sendTemperatureMessage(temp);
      }
      
      // Send status message
      sendStatusMessage("Normal Operation", 0x00);
      
      // Send fault information
      uint8_t fault = readRegister(REG_FAULT_STATUS);
      if (fault != 0) {
        sendFaultMessage(fault);
      }
      
      // Send heartbeat
      sendHeartbeat();
      
      message_count++;
    }
    
    // Read and decode faults
    uint8_t fault = readRegister(REG_FAULT_STATUS);
    if (fault != 0) {
      Serial.print("Fault detected: 0x");
      Serial.println(fault, HEX);
      decodeFaultStatus(fault);
    } else {
      Serial.println("No faults detected");
    }
    
    // Show message tracking info
    Serial.println("\n=== MESSAGE TRACKING ===");
    Serial.print("Messages received from Pi: ");
    Serial.println(messages_received);
    if (messages_received > 0) {
      Serial.print("Last received: ID=0x");
      Serial.print(last_received_id, HEX);
      Serial.print(" Data=");
      Serial.print(last_received_data);
      Serial.print(" (");
      Serial.print((millis() - last_received_time) / 1000);
      Serial.println(" seconds ago)");
    } else {
      Serial.println("No messages received from Pi yet");
    }
    Serial.println("========================");
    
    lastRead = millis();
  }
}

// Enhanced CAN message functions using TWAI
void sendTemperatureMessage(float temperature) {
  // Convert temperature to fixed-point (multiply by 100 for 2 decimal places), rounded
  int16_t temp_fixed = (int16_t)lroundf(temperature * 100.0f);
  int16_t max_fixed  = (int16_t)lroundf(max_temperature * 100.0f);
  int16_t min_fixed  = (int16_t)lroundf(min_temperature * 100.0f);
  
  // Prepare CAN data (8 bytes)
  twai_message_t msg;
  msg.identifier = CAN_ID_PT100_TEMP;
  msg.data_length_code = 8;
  msg.flags = TWAI_MSG_FLAG_NONE;
  msg.data[0] = (uint8_t)((temp_fixed >> 8) & 0xFF);      // High byte
  msg.data[1] = (uint8_t)(temp_fixed & 0xFF);             // Low byte
  msg.data[2] = (uint8_t)((max_fixed >> 8) & 0xFF);       // Max temp high
  msg.data[3] = (uint8_t)(max_fixed & 0xFF);              // Max temp low
  msg.data[4] = (uint8_t)((min_fixed >> 8) & 0xFF);       // Min temp high
  msg.data[5] = (uint8_t)(min_fixed & 0xFF);              // Min temp low
  msg.data[6] = message_count & 0xFF;                    // Message count low
  msg.data[7] = (message_count >> 8) & 0xFF;             // Message count high
  
  esp_err_t send_result = twai_transmit(&msg, pdMS_TO_TICKS(1000));
  Serial.print("Temperature send result: ");
  Serial.print(send_result);
  Serial.print(" (");
  Serial.print(esp_err_to_name(send_result));
  Serial.println(")");
  if (send_result == ESP_OK) {
    Serial.print("OK Temperature data sent over CAN successfully! [");
    Serial.print(current_bitrate);
    Serial.println("]");
  } else {
    Serial.println("FAIL Temperature CAN send failed!");
    if (can_initialized) {
      twai_status_info_t st; if (twai_get_status_info(&st) == ESP_OK) {
        Serial.print("State="); Serial.print((int)st.state);
        Serial.print(" TXerr="); Serial.print((int)st.tx_error_counter);
        Serial.print(" RXerr="); Serial.println((int)st.rx_error_counter);
      }
    }
  }
}

void sendStatusMessage(const char* status, uint8_t status_code) {
  twai_message_t msg;
  msg.identifier = CAN_ID_STATUS;
  msg.data_length_code = 8;
  msg.flags = TWAI_MSG_FLAG_NONE;
  msg.data[0] = status_code;                    // Status code
  msg.data[1] = max31865_working ? 0x01 : 0x00; // MAX31865 status
  msg.data[2] = can_initialized ? 0x01 : 0x00;  // CAN status
  int16_t last_fixed = (int16_t)lroundf(last_valid_temp * 100.0f);
  msg.data[3] = (uint8_t)((last_fixed >> 8) & 0xFF);  // Last temp high
  msg.data[4] = (uint8_t)(last_fixed & 0xFF);         // Last temp low
  msg.data[5] = message_count & 0xFF;           // Message count low
  msg.data[6] = (message_count >> 8) & 0xFF;    // Message count high
  msg.data[7] = 0x00;                           // Reserved
  
  esp_err_t send_result = twai_transmit(&msg, pdMS_TO_TICKS(1000));
  Serial.print("Status send result: ");
  Serial.print(send_result);
  Serial.print(" (");
  Serial.print(esp_err_to_name(send_result));
  Serial.println(")");
  if (send_result == ESP_OK) {
    Serial.print("OK Status message sent: ");
    Serial.print(status);
    Serial.print(" [");
    Serial.print(current_bitrate);
    Serial.println("]");
  } else {
    Serial.println("FAIL Status CAN send failed!");
    if (can_initialized) {
      twai_status_info_t st; if (twai_get_status_info(&st) == ESP_OK) {
        Serial.print("State="); Serial.print((int)st.state);
        Serial.print(" TXerr="); Serial.print((int)st.tx_error_counter);
        Serial.print(" RXerr="); Serial.println((int)st.rx_error_counter);
      }
    }
  }
}

void sendFaultMessage(uint8_t fault_status) {
  twai_message_t msg;
  msg.identifier = CAN_ID_FAULTS;
  msg.data_length_code = 8;
  msg.flags = TWAI_MSG_FLAG_NONE;
  msg.data[0] = fault_status;                   // Fault status
  int16_t last_fixed = (int16_t)lroundf(last_valid_temp * 100.0f);
  msg.data[1] = (uint8_t)((last_fixed >> 8) & 0xFF);  // Last temp high
  msg.data[2] = (uint8_t)(last_fixed & 0xFF);         // Last temp low
  msg.data[3] = message_count & 0xFF;           // Message count low
  msg.data[4] = (message_count >> 8) & 0xFF;    // Message count high
  msg.data[5] = 0x00;                           // Reserved
  msg.data[6] = 0x00;                           // Reserved
  msg.data[7] = 0x00;                           // Reserved
  
  esp_err_t fr = twai_transmit(&msg, pdMS_TO_TICKS(1000));
  if (fr == ESP_OK) {
    Serial.println("OK Fault message sent over CAN!");
  } else {
    Serial.print("FAIL Fault CAN send failed! ");
    Serial.println(esp_err_to_name(fr));
  }
}

void sendConfigMessage() {
  twai_message_t msg;
  msg.identifier = CAN_ID_CONFIG;
  msg.data_length_code = 8;
  msg.flags = TWAI_MSG_FLAG_NONE;
  msg.data[0] = 0x01;                           // Config version
  msg.data[1] = (uint8_t)(RREF * 100) >> 8;     // RREF high (430.0 -> 43000)
  msg.data[2] = (uint8_t)(RREF * 100) & 0xFF;   // RREF low
  msg.data[3] = 0x04;                           // 4-wire mode
  msg.data[4] = 0x32;                           // 50Hz filter
  msg.data[5] = 0x00;                           // Reserved
  msg.data[6] = 0x00;                           // Reserved
  msg.data[7] = 0x00;                           // Reserved
  
  if (twai_transmit(&msg, pdMS_TO_TICKS(1000)) == ESP_OK) {
    Serial.println("OK Configuration message sent over CAN!");
  } else {
    Serial.println("FAIL Configuration CAN send failed!");
  }
}

void sendHeartbeat() {
  twai_message_t msg;
  msg.identifier = CAN_ID_HEARTBEAT;
  msg.data_length_code = 8;
  msg.flags = TWAI_MSG_FLAG_NONE;
  msg.data[0] = 0xAA;                           // Heartbeat signature
  msg.data[1] = 0x55;                           // Heartbeat signature
  msg.data[2] = message_count & 0xFF;           // Message count low
  msg.data[3] = (message_count >> 8) & 0xFF;    // Message count high
  msg.data[4] = (uint8_t)(millis() >> 24);      // Uptime byte 1
  msg.data[5] = (uint8_t)(millis() >> 16);      // Uptime byte 2
  msg.data[6] = (uint8_t)(millis() >> 8);       // Uptime byte 3
  msg.data[7] = (uint8_t)(millis());            // Uptime byte 4
  
  esp_err_t send_result = twai_transmit(&msg, pdMS_TO_TICKS(1000));
  Serial.print("Heartbeat send result: ");
  Serial.print(send_result);
  Serial.print(" (");
  Serial.print(esp_err_to_name(send_result));
  Serial.println(")");
  if (send_result == ESP_OK) {
    Serial.print("OK Heartbeat sent over CAN! [");
    Serial.print(current_bitrate);
    Serial.println("]");
  } else {
    Serial.println("FAIL Heartbeat CAN send failed!");
    if (can_initialized) {
      twai_status_info_t st; if (twai_get_status_info(&st) == ESP_OK) {
        Serial.print("State="); Serial.print((int)st.state);
        Serial.print(" TXerr="); Serial.print((int)st.tx_error_counter);
        Serial.print(" RXerr="); Serial.println((int)st.rx_error_counter);
      }
    }
  }
}

void configureMAX31865() {
  // Clear any existing faults first
  writeRegister(REG_CONFIG, CONFIG_CLEAR_FAULT);
  delay(100);
  
  // Configure: Vbias on, auto convert, 50Hz filter (4-wire)
  uint8_t config = CONFIG_BIAS | CONFIG_AUTO_CONVERT | CONFIG_50HZ_FILTER;
  // Note: 4-wire doesn't need CONFIG_3WIRE flag
  Serial.print("Writing config: 0x");
  Serial.println(config, HEX);
  writeRegister(REG_CONFIG, config);
  delay(500);  // Give it time to settle
  
  // Verify configuration
  uint8_t read_config = readRegister(REG_CONFIG);
  Serial.print("Config readback: 0x");
  Serial.println(read_config, HEX);
  
  if (read_config == config) {
    Serial.println("OK Configuration successful");
  } else {
    Serial.println("FAIL Configuration mismatch!");
  }
}

float readTemperature() {
  // Read RTD value
  uint8_t msb = readRegister(REG_RTD_MSB);
  uint8_t lsb = readRegister(REG_RTD_LSB);
  uint16_t rtd = ((msb << 8) | lsb) >> 1;
  
  if (rtd == 0) {
    return -999.0;  // Invalid reading
  }
  
  // Calculate resistance
  float resistance = (rtd * RREF) / 32768.0;
  
  // PT100 calculation
  try {
    float discriminant = A*A - 4*B*(1 - resistance/100.0);
    if (discriminant < 0) {
      return -999.0;
    }
    float temp = (-A + sqrt(discriminant)) / (2*B);
    return temp;
  } catch (...) {
    return -999.0;
  }
}

void decodeFaultStatus(uint8_t fault) {
  if (fault & 0x80) Serial.println("  - RTD High Threshold");
  if (fault & 0x40) Serial.println("  - RTD Low Threshold"); 
  if (fault & 0x20) Serial.println("  - REFIN- > 0.85 x VDD");
  if (fault & 0x10) Serial.println("  - REFIN- < 0.85 x VDD (FORCE- open)");
  if (fault & 0x08) Serial.println("  - RTDIN- < 0.85 x VDD (FORCE- open)");
  if (fault & 0x04) Serial.println("  - Overvoltage/Undervoltage");
}

uint8_t readRegister(uint8_t reg) {
  digitalWrite(MAX1_CS, LOW);
  delayMicroseconds(10);
  
  uint8_t value = 0;
  
  // Send register address (read command)
  for (int i = 7; i >= 0; i--) {
    digitalWrite(MAX1_SCK, LOW);
    digitalWrite(MAX1_MOSI, (reg & 0x7F) & (1 << i) ? HIGH : LOW);
    delayMicroseconds(1);
    digitalWrite(MAX1_SCK, HIGH);
    delayMicroseconds(1);
  }
  
  // Read data
  for (int i = 7; i >= 0; i--) {
    digitalWrite(MAX1_SCK, LOW);
    delayMicroseconds(1);
    digitalWrite(MAX1_SCK, HIGH);
    delayMicroseconds(1);
    if (digitalRead(MAX1_MISO) == HIGH) {
      value |= (1 << i);
    }
  }
  
  delayMicroseconds(10);
  digitalWrite(MAX1_CS, HIGH);
  
  return value;
}

void writeRegister(uint8_t reg, uint8_t value) {
  digitalWrite(MAX1_CS, LOW);
  delayMicroseconds(10);
  
  // Send register address (write command)
  for (int i = 7; i >= 0; i--) {
    digitalWrite(MAX1_SCK, LOW);
    digitalWrite(MAX1_MOSI, (reg | 0x80) & (1 << i) ? HIGH : LOW);
    delayMicroseconds(1);
    digitalWrite(MAX1_SCK, HIGH);
    delayMicroseconds(1);
  }
  
  // Send data
  for (int i = 7; i >= 0; i--) {
    digitalWrite(MAX1_SCK, LOW);
    digitalWrite(MAX1_MOSI, value & (1 << i) ? HIGH : LOW);
    delayMicroseconds(1);
    digitalWrite(MAX1_SCK, HIGH);
    delayMicroseconds(1);
  }
  
  delayMicroseconds(10);
  digitalWrite(MAX1_CS, HIGH);
}
