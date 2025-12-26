/** Types for sensor data. */

export interface SensorData {
  sensor: string;
  value: number;
  time: string;
  unit: string;
}

export interface SensorDataResponse {
  [sensorName: string]: {
    data: Array<{ time: string; value: number }>;
    unit: string;
    sensor_name: string;
  };
}

