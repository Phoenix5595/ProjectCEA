/** Row shape for ZoneConfig climate periods table and timeline (matches API). */
export interface ClimatePeriod {
  id?: number
  period_name: string
  start_time: string
  end_time: string
  ramp_minutes: number
  heating_setpoint: number | null
  cooling_setpoint: number | null
  vpd_setpoint: number | null
  co2_setpoint: number | null
  details: string
}
