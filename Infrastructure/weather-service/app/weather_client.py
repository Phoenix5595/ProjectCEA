"""Weather API client for fetching METAR data."""
import logging
import httpx
from typing import Dict, Any, Optional
from datetime import datetime
import math

logger = logging.getLogger(__name__)


class WeatherClient:
    """Client for fetching weather data from Aviation Weather Center METAR API."""
    
    def __init__(self, api_url: str, station_icao: str):
        """Initialize weather client.
        
        Args:
            api_url: Base URL for METAR API
            station_icao: ICAO code for weather station (e.g., "CYUL")
        """
        self.api_url = api_url
        self.station_icao = station_icao
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def close(self) -> None:
        """Close HTTP client."""
        await self.client.aclose()
    
    async def fetch_metar(self) -> Optional[Dict[str, Any]]:
        """Fetch METAR data for the configured station.
        
        Returns:
            Dictionary with parsed weather data, or None if fetch/parse failed
        """
        try:
            # Aviation Weather Center METAR API
            # Format: https://aviationweather.gov/api/data/metar?ids=STATION&format=json
            url = f"{self.api_url}?ids={self.station_icao}&format=json"
            
            logger.debug(f"Fetching METAR from {url}")
            response = await self.client.get(url)
            response.raise_for_status()
            
            data = response.json()
            
            # API returns a list of METAR reports
            if not data or not isinstance(data, list) or len(data) == 0:
                logger.warning(f"No METAR data returned for {self.station_icao}")
                return None
            
            # Get the first (most recent) report
            metar_report = data[0]
            
            # Parse METAR data
            weather_data = self._parse_metar(metar_report)
            
            if weather_data:
                logger.info(f"Successfully fetched weather data for {self.station_icao}")
            else:
                logger.warning(f"Failed to parse METAR data for {self.station_icao}")
            
            return weather_data
            
        except httpx.TimeoutException:
            logger.error(f"Timeout fetching METAR data for {self.station_icao}")
            return None
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching METAR: {e.response.status_code}")
            return None
        except Exception as e:
            logger.error(f"Error fetching METAR data: {e}", exc_info=True)
            return None
    
    def _parse_metar(self, metar_report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse METAR report into structured weather data.
        
        Args:
            metar_report: METAR report dictionary from API
            
        Returns:
            Dictionary with weather parameters, or None if parsing failed
        """
        try:
            weather_data = {}
            
            # Extract raw METAR text
            raw_text = metar_report.get('rawOb', '')
            if not raw_text:
                logger.warning("No raw METAR text found in response")
                return None
            
            # Parse temperature (in Celsius)
            temp_c = metar_report.get('temp')
            if temp_c is not None:
                weather_data['temperature'] = float(temp_c)
            else:
                logger.warning("Temperature not found in METAR")
            
            # Parse dewpoint (in Celsius)
            dewp_c = metar_report.get('dewp')
            if dewp_c is not None:
                weather_data['dewpoint'] = float(dewp_c)
            else:
                logger.warning("Dewpoint not found in METAR")
            
            # Calculate relative humidity from temperature and dewpoint
            if 'temperature' in weather_data and 'dewpoint' in weather_data:
                rh = self._calculate_rh(
                    weather_data['temperature'],
                    weather_data['dewpoint']
                )
                weather_data['relative_humidity'] = rh
            
            # Parse pressure (in hPa, may be in inches Hg in some formats)
            altim = metar_report.get('altim')
            if altim is not None:
                # altim is typically in inches of mercury, convert to hPa
                # 1 inHg = 33.8639 hPa
                pressure_hpa = float(altim) * 33.8639
                weather_data['pressure'] = round(pressure_hpa, 2)
            else:
                logger.warning("Pressure not found in METAR")
            
            # Parse wind speed and direction
            wdir = metar_report.get('wdir')
            wspd = metar_report.get('wspd')
            
            if wdir is not None:
                weather_data['wind_direction'] = int(wdir)
            else:
                logger.warning("Wind direction not found in METAR")
            
            if wspd is not None:
                # Wind speed is in knots, convert to m/s
                # 1 knot = 0.514444 m/s
                wind_speed_ms = float(wspd) * 0.514444
                weather_data['wind_speed'] = round(wind_speed_ms, 2)
            else:
                logger.warning("Wind speed not found in METAR")
            
            # Parse precipitation (if available)
            # METAR may include precipitation in remarks or wxString
            wx_string = metar_report.get('wxString', '')
            precip = metar_report.get('precip')
            
            if precip is not None:
                # Precipitation in inches, convert to mm
                # 1 inch = 25.4 mm
                precip_mm = float(precip) * 25.4
                weather_data['precipitation'] = round(precip_mm, 2)
            elif 'RA' in wx_string or 'SN' in wx_string or 'DZ' in wx_string:
                # Indicates precipitation but no amount
                weather_data['precipitation'] = 0.0
            else:
                # No precipitation data available
                weather_data['precipitation'] = None
            
            # Add timestamp
            obs_time = metar_report.get('obsTime')
            if obs_time:
                try:
                    # Parse ISO format timestamp
                    # Handle various formats: "2024-01-15T10:30:00Z" or "2024-01-15T10:30:00+00:00"
                    time_str = str(obs_time)
                    if time_str.endswith('Z'):
                        time_str = time_str.replace('Z', '+00:00')
                    weather_data['timestamp'] = datetime.fromisoformat(time_str)
                except Exception as e:
                    logger.warning(f"Failed to parse timestamp {obs_time}: {e}, using current time")
                    weather_data['timestamp'] = datetime.now()
            else:
                weather_data['timestamp'] = datetime.now()
            
            return weather_data
            
        except Exception as e:
            logger.error(f"Error parsing METAR data: {e}", exc_info=True)
            return None
    
    def _calculate_rh(self, temp_c: float, dewpoint_c: float) -> float:
        """Calculate relative humidity from temperature and dewpoint.
        
        Uses the Magnus formula approximation.
        
        Args:
            temp_c: Temperature in Celsius
            dewpoint_c: Dewpoint in Celsius
            
        Returns:
            Relative humidity as percentage (0-100)
        """
        try:
            # Magnus formula constants
            a = 17.27
            b = 237.7
            
            # Calculate saturation vapor pressure at temperature
            es_t = 6.112 * math.exp((a * temp_c) / (b + temp_c))
            
            # Calculate actual vapor pressure at dewpoint
            es_d = 6.112 * math.exp((a * dewpoint_c) / (b + dewpoint_c))
            
            # Relative humidity = (actual vapor pressure / saturation vapor pressure) * 100
            rh = (es_d / es_t) * 100.0
            
            # Clamp to 0-100%
            rh = max(0.0, min(100.0, rh))
            
            return round(rh, 2)
        except Exception as e:
            logger.error(f"Error calculating RH: {e}")
            return 0.0

