#include <CAN.h>

// CAN Bus pin definitions for MCP2515
#define CAN_CS 15   // CS pin (D15) for MCP CAN board

// CAN Message IDs
#define CAN_ID_PT100_TEMP 0x101    // Temperature data
#define CAN_ID_STATUS 0x102        // Status and health
#define CAN_ID_FAULTS 0x103        // Fault information
#define CAN_ID_CONFIG 0x104        // Configuration data
#define CAN_ID_HEARTBEAT 0x105     // Heartbeat message

// Status variables
bool can_initialized = false;
unsigned long message_count = 0;

void setup() {
  Serial.begin(115200);
  delay(2000);  // Wait for Serial
  
  Serial.println("=== ESP32 Alternative CAN Test ===");
  Serial.println("Using CAN.h library instead of mcp_can.h");
  
  // Initialize CAN Bus with alternative library
  Serial.println("Initializing CAN Bus...");
  
  // Set CS pin for MCP2515
  CAN.setPins(CAN_CS, -1);  // CS pin, no INT pin
  
  // Start CAN bus at 500 kbps
  if (!CAN.begin(500E3)) {
    Serial.println("FAIL CAN initialization failed!");
    can_initialized = false;
  } else {
    Serial.println("OK CAN initialization successful!");
    can_initialized = true;
    
    // Send test message immediately
    Serial.println("Sending test message...");
    CAN.beginPacket(0x123);
    CAN.write(0xAA);
    CAN.write(0xBB);
    CAN.write(0xCC);
    CAN.write(0xDD);
    CAN.endPacket();
    Serial.println("Test message sent!");
  }
}

void loop() {
  // Check for incoming CAN messages
  if (CAN.parsePacket()) {
    Serial.println("\n=== RECEIVED CAN MESSAGE ===");
    Serial.print("Received ID: 0x");
    Serial.print(CAN.packetId(), HEX);
    Serial.print(" Length: ");
    Serial.println(CAN.packetDlc());
    Serial.print("Data: ");
    while (CAN.available()) {
      Serial.print(CAN.read(), HEX);
      Serial.print(" ");
    }
    Serial.println();
    Serial.println("=== END RECEIVED MESSAGE ===\n");
  }
  
  // Send test messages every 5 seconds
  static unsigned long lastSend = 0;
  if (millis() - lastSend >= 5000 && can_initialized) {
    Serial.println("\n=== Sending Test Messages ===");
    
    // Send temperature message
    CAN.beginPacket(CAN_ID_PT100_TEMP);
    CAN.write(0x08);  // Temperature high byte
    CAN.write(0x55);  // Temperature low byte
    CAN.write(0x00);  // Max temp high
    CAN.write(0x59);  // Max temp low
    CAN.write(0x00);  // Min temp high
    CAN.write(0x52);  // Min temp low
    CAN.write(message_count & 0xFF);  // Message count low
    CAN.write((message_count >> 8) & 0xFF);  // Message count high
    CAN.endPacket();
    Serial.println("OK Temperature message sent!");
    
    // Send status message
    CAN.beginPacket(CAN_ID_STATUS);
    CAN.write(0x00);  // Status code
    CAN.write(0x01);  // MAX31865 status
    CAN.write(0x01);  // CAN status
    CAN.write(0x00);  // Last temp high
    CAN.write(0x55);  // Last temp low
    CAN.write(message_count & 0xFF);  // Message count low
    CAN.write((message_count >> 8) & 0xFF);  // Message count high
    CAN.write(0x00);  // Reserved
    CAN.endPacket();
    Serial.println("OK Status message sent!");
    
    // Send heartbeat
    CAN.beginPacket(CAN_ID_HEARTBEAT);
    CAN.write(0xAA);  // Heartbeat signature
    CAN.write(0x55);  // Heartbeat signature
    CAN.write(message_count & 0xFF);  // Message count low
    CAN.write((message_count >> 8) & 0xFF);  // Message count high
    CAN.write((millis() >> 24) & 0xFF);  // Uptime byte 1
    CAN.write((millis() >> 16) & 0xFF);  // Uptime byte 2
    CAN.write((millis() >> 8) & 0xFF);   // Uptime byte 3
    CAN.write(millis() & 0xFF);          // Uptime byte 4
    CAN.endPacket();
    Serial.println("OK Heartbeat sent!");
    
    message_count++;
    lastSend = millis();
  }
}
