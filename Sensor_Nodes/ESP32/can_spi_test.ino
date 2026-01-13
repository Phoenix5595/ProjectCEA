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
#define CAN_ID_PT100 0x101  // CAN ID for PT100 temperature data

// Reference resistor value (Ohms) - for 430Ω board (Adafruit)
#define RREF 430.0

// PT100 Callendar-Van Dusen coefficients
#define A 3.9083e-3
#define B -5.775e-7

// CAN Bus object
MCP_CAN CAN(CAN_CS);

void setup() {
  Serial.begin(115200);
  delay(2000);  // Wait for Serial
  
  Serial.println("=== ESP32 STARTING ===");
  Serial.println("ESP32 MAX31865 + CAN Bus Test...");
  
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
  
  // Initialize CAN Bus
  Serial.println("Step 5: About to initialize CAN...");
  Serial.println("Initializing CAN Bus...");
  while (CAN_OK != CAN.begin(MCP_ANY, CAN_500KBPS, MCP_8MHZ)) {
    Serial.println("CAN BUS Shield init fail");
    delay(100);
  }
  Serial.println("Step 6: CAN BUS Shield init ok!");
  
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
    Serial.println("✓ SPI communication working!");
  } else {
    Serial.println("✗ SPI communication failed!");
  }
  
  // Configure MAX31865 for PT100 operation
  Serial.println("\nStep 13: Configuring MAX31865...");
  Serial.println("=== Configuring MAX31865 ===");
  configureMAX31865();
  
  Serial.println("Step 14: Setup complete!");
}

void loop() {
  // Read all registers and temperature every 2 seconds
  static unsigned long lastRead = 0;
  if (millis() - lastRead >= 2000) {
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
    Serial.println(" °C");
    
    // Send temperature data over CAN Bus
    if (temp != -999.0) {  // Only send valid readings
      // Convert temperature to fixed-point (multiply by 100 for 2 decimal places)
      int16_t temp_fixed = (int16_t)(temp * 100);
      
      // Prepare CAN data (8 bytes)
      uint8_t can_data[8];
      can_data[0] = temp_fixed >> 8;      // High byte
      can_data[1] = temp_fixed & 0xFF;    // Low byte
      can_data[2] = 0;  // Fill remaining bytes with 0
      can_data[3] = 0;
      can_data[4] = 0;
      can_data[5] = 0;
      can_data[6] = 0;
      can_data[7] = 0;
      
      // Send over CAN
      if (CAN.sendMsgBuf(CAN_ID_PT100, 0, 8, can_data) == CAN_OK) {
        Serial.println("Temperature data sent over CAN successfully!");
      } else {
        Serial.println("CAN send failed!");
      }
    }
    
    // Send a simple test message to verify CAN is working
    uint8_t test_data[8] = {0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x11, 0x22};
    if (CAN.sendMsgBuf(0x123, 0, 8, test_data) == CAN_OK) {
      Serial.println("Test message sent successfully!");
    } else {
      Serial.println("Test message failed!");
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
    
    lastRead = millis();
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
    Serial.println("✓ Configuration successful");
  } else {
    Serial.println("✗ Configuration mismatch!");
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

