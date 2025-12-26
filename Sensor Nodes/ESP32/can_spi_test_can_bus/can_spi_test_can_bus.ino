#include <mcp_can.h>

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

// CAN Bus pin definitions
#define CAN_CS 15   // CS pin (D15) for MCP CAN board

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

// CAN Bus object
MCP_CAN CAN(CAN_CS);

// Status variables
bool can_initialized = false;
bool max31865_working = false;
unsigned long message_count = 0;
unsigned long last_can_send = 0;
float max_temperature = -999.0;
float min_temperature = 999.0;
float last_valid_temp = -999.0;
String current_bitrate = "unknown";

// Message tracking
unsigned long messages_received = 0;
unsigned long last_received_id = 0;
unsigned long last_received_time = 0;
String last_received_data = "";

void setup() {
  Serial.begin(115200);
  delay(2000);  // Wait for Serial
  
  Serial.println("=== ESP32 MAX31865 + Enhanced CAN Bus ===");
  Serial.println("Starting enhanced version with multiple CAN message types...");
  
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
  
  // Initialize CAN Bus with enhanced error handling
  Serial.println("Step 5: Initializing enhanced CAN Bus...");
  int can_init_attempts = 0;
  
  // Try different bitrates
  byte bitrates[] = {CAN_125KBPS, CAN_250KBPS, CAN_500KBPS};
  String bitrate_names[] = {"125kbps", "250kbps", "500kbps"};
  bool can_init_success = false;
  String successful_bitrate = "none";
  
  for (int b = 0; b < 3; b++) {
    Serial.print("Trying ");
    Serial.print(bitrate_names[b]);
    Serial.println("...");
    
    can_init_attempts = 0;
    while (CAN_OK != CAN.begin(MCP_ANY, bitrates[b], MCP_8MHZ)) {
      can_init_attempts++;
      Serial.print("CAN BUS Shield init fail, attempt ");
      Serial.println(can_init_attempts);
      if (can_init_attempts > 5) {
        Serial.println("Failed, trying next bitrate...");
        break;
      }
      delay(100);
    }
    
    if (can_init_attempts <= 5) {
      Serial.print("SUCCESS with ");
      Serial.println(bitrate_names[b]);
      successful_bitrate = bitrate_names[b];
      can_init_success = true;
      break;
    }
  }
  
  if (can_init_success) {
    Serial.println("Step 6: CAN BUS Shield init ok!");
    Serial.print("FINAL BITRATE: ");
    Serial.println(successful_bitrate);
    current_bitrate = successful_bitrate;
    can_initialized = true;
    
    // Test CAN transmission immediately
    Serial.println("Step 6.1: Testing CAN transmission...");
    uint8_t test_data[8] = {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x00, 0x11};
    byte send_result = CAN.sendMsgBuf(0x123, 0, 8, test_data);
    Serial.print("Test send result: ");
    Serial.println(send_result);
    if (send_result == CAN_OK) {
      Serial.println("OK Test message sent successfully!");
    } else {
      Serial.println("FAIL Test message send failed!");
    }
    
    // Send initial status message
    sendStatusMessage("ESP32 Starting", 0x01);
  } else {
    Serial.println("Step 6: CAN BUS Shield init failed on all bitrates!");
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
  if (CAN_MSGAVAIL == CAN.checkReceive()) {
    Serial.println("\n=== RECEIVED CAN MESSAGE ===");
    long unsigned int rxId;
    unsigned char len = 0;
    unsigned char rxBuf[8];
    
    CAN.readMsgBuf(&rxId, &len, rxBuf);
    
    // Track the message
    messages_received++;
    last_received_id = rxId;
    last_received_time = millis();
    last_received_data = "";
    for(int i = 0; i < len; i++) {
      if (rxBuf[i] < 0x10) last_received_data += "0";
      last_received_data += String(rxBuf[i], HEX);
      if (i < len-1) last_received_data += " ";
    }
    
    Serial.print("Received ID: 0x");
    Serial.print(rxId, HEX);
    Serial.print(" Length: ");
    Serial.print(len);
    Serial.print(" Data: ");
    Serial.println(last_received_data);
    Serial.println("=== END RECEIVED MESSAGE ===\n");
  }
  
  // Read all registers and temperature every 10 seconds
  static unsigned long lastRead = 0;
  if (millis() - lastRead >= 10000) {
    Serial.println("\n=== Register Dump & Temperature ===");
    
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

// Enhanced CAN message functions
void sendTemperatureMessage(float temperature) {
  // Convert temperature to fixed-point (multiply by 100 for 2 decimal places)
  int16_t temp_fixed = (int16_t)(temperature * 100);
  
  // Prepare CAN data (8 bytes)
  uint8_t can_data[8];
  can_data[0] = temp_fixed >> 8;      // High byte
  can_data[1] = temp_fixed & 0xFF;    // Low byte
  can_data[2] = (uint8_t)(max_temperature * 100) >> 8;  // Max temp high
  can_data[3] = (uint8_t)(max_temperature * 100) & 0xFF; // Max temp low
  can_data[4] = (uint8_t)(min_temperature * 100) >> 8;  // Min temp high
  can_data[5] = (uint8_t)(min_temperature * 100) & 0xFF; // Min temp low
  can_data[6] = message_count & 0xFF;                    // Message count low
  can_data[7] = (message_count >> 8) & 0xFF;             // Message count high
  
  byte send_result = CAN.sendMsgBuf(CAN_ID_PT100_TEMP, 0, 8, can_data);
  Serial.print("Temperature send result: ");
  Serial.println(send_result);
  if (send_result == CAN_OK) {
    Serial.print("OK Temperature data sent over CAN successfully! [");
    Serial.print(current_bitrate);
    Serial.println("]");
  } else {
    Serial.println("FAIL Temperature CAN send failed!");
  }
}

void sendStatusMessage(const char* status, uint8_t status_code) {
  uint8_t can_data[8];
  can_data[0] = status_code;                    // Status code
  can_data[1] = max31865_working ? 0x01 : 0x00; // MAX31865 status
  can_data[2] = can_initialized ? 0x01 : 0x00;  // CAN status
  can_data[3] = (uint8_t)(last_valid_temp * 100) >> 8;  // Last temp high
  can_data[4] = (uint8_t)(last_valid_temp * 100) & 0xFF; // Last temp low
  can_data[5] = message_count & 0xFF;           // Message count low
  can_data[6] = (message_count >> 8) & 0xFF;    // Message count high
  can_data[7] = 0x00;                           // Reserved
  
  byte send_result = CAN.sendMsgBuf(CAN_ID_STATUS, 0, 8, can_data);
  Serial.print("Status send result: ");
  Serial.println(send_result);
  if (send_result == CAN_OK) {
    Serial.print("OK Status message sent: ");
    Serial.print(status);
    Serial.print(" [");
    Serial.print(current_bitrate);
    Serial.println("]");
  } else {
    Serial.println("FAIL Status CAN send failed!");
  }
}

void sendFaultMessage(uint8_t fault_status) {
  uint8_t can_data[8];
  can_data[0] = fault_status;                   // Fault status
  can_data[1] = (uint8_t)(last_valid_temp * 100) >> 8;  // Last temp high
  can_data[2] = (uint8_t)(last_valid_temp * 100) & 0xFF; // Last temp low
  can_data[3] = message_count & 0xFF;           // Message count low
  can_data[4] = (message_count >> 8) & 0xFF;    // Message count high
  can_data[5] = 0x00;                           // Reserved
  can_data[6] = 0x00;                           // Reserved
  can_data[7] = 0x00;                           // Reserved
  
  if (CAN.sendMsgBuf(CAN_ID_FAULTS, 0, 8, can_data) == CAN_OK) {
    Serial.println("✓ Fault message sent over CAN!");
  } else {
    Serial.println("✗ Fault CAN send failed!");
  }
}

void sendConfigMessage() {
  uint8_t can_data[8];
  can_data[0] = 0x01;                           // Config version
  can_data[1] = (uint8_t)(RREF * 100) >> 8;     // RREF high (430.0 -> 43000)
  can_data[2] = (uint8_t)(RREF * 100) & 0xFF;   // RREF low
  can_data[3] = 0x04;                           // 4-wire mode
  can_data[4] = 0x32;                           // 50Hz filter
  can_data[5] = 0x00;                           // Reserved
  can_data[6] = 0x00;                           // Reserved
  can_data[7] = 0x00;                           // Reserved
  
  if (CAN.sendMsgBuf(CAN_ID_CONFIG, 0, 8, can_data) == CAN_OK) {
    Serial.println("✓ Configuration message sent over CAN!");
  } else {
    Serial.println("✗ Configuration CAN send failed!");
  }
}

void sendHeartbeat() {
  uint8_t can_data[8];
  can_data[0] = 0xAA;                           // Heartbeat signature
  can_data[1] = 0x55;                           // Heartbeat signature
  can_data[2] = message_count & 0xFF;           // Message count low
  can_data[3] = (message_count >> 8) & 0xFF;    // Message count high
  can_data[4] = (uint8_t)(millis() >> 24);      // Uptime byte 1
  can_data[5] = (uint8_t)(millis() >> 16);      // Uptime byte 2
  can_data[6] = (uint8_t)(millis() >> 8);       // Uptime byte 3
  can_data[7] = (uint8_t)(millis());            // Uptime byte 4
  
  byte send_result = CAN.sendMsgBuf(CAN_ID_HEARTBEAT, 0, 8, can_data);
  Serial.print("Heartbeat send result: ");
  Serial.println(send_result);
  if (send_result == CAN_OK) {
    Serial.print("OK Heartbeat sent over CAN! [");
    Serial.print(current_bitrate);
    Serial.println("]");
  } else {
    Serial.println("FAIL Heartbeat CAN send failed!");
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
