"""Weather API client for fetching METAR data."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from shared.climate import calculate_rh_from_dewpoint
from shared.infra_logging import get_logger
from shared.retry import retry_async

logger = get_logger(__name__)

# Transient errors worth retrying. 5xx is server-side and usually clears on
# its own; timeouts / connect / read errors are network-side. 4xx is NOT in
# this list - if we send a bad station code, retrying will not fix it.
_TRANSIENT_NETWORK_ERRORS: tuple[type[BaseException], ...] = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)


class _TransientHTTPError(Exception):
    """Wraps an HTTPStatusError we want the retry loop to re-attempt."""

    def __init__(self, status_code: int, url: str):
        super().__init__(f"upstream returned {status_code} for {url}")
        self.status_code = status_code


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

    async def fetch_metar(self) -> dict[str, Any] | None:
        """Fetch METAR data for the configured station.

        Retries transient network failures (timeout / connect / read /
        remote-protocol error) and 5xx upstream responses with exponential
        backoff + full jitter. 4xx responses are NOT retried (they signal a
        contract problem on our side, e.g. a bad station code) and parse
        failures are NOT retried (the upstream already gave us a response;
        another fetch will not change the data shape).

        Returns:
            Dictionary with parsed weather data, or None if fetch/parse failed
            after all retry attempts.
        """
        try:
            metar_report = await retry_async(
                self._do_fetch_metar,
                retry_on=(*_TRANSIENT_NETWORK_ERRORS, _TransientHTTPError),
                max_attempts=3,
                base_delay=1.0,
                max_delay=10.0,
                label=f"weather METAR {self.station_icao}",
            )
        except _TransientHTTPError as e:
            logger.error(
                f"HTTP error fetching METAR for {self.station_icao} after retries: {e.status_code}"
            )
            return None
        except _TRANSIENT_NETWORK_ERRORS as e:
            logger.error(
                f"Network error fetching METAR for {self.station_icao} after retries: "
                f"{type(e).__name__}: {e}"
            )
            return None
        except httpx.HTTPStatusError as e:
            # Non-retryable HTTP error (4xx).
            logger.error(
                f"HTTP {e.response.status_code} fetching METAR for {self.station_icao} "
                "(not retried; check station code / API URL)"
            )
            return None
        except Exception as e:
            logger.error(f"Error fetching METAR data: {e}", exc_info=True)
            return None

        if metar_report is None:
            # Upstream responded but had no rows for our station.
            return None

        weather_data = self._parse_metar(metar_report)
        if weather_data:
            logger.info(f"Successfully fetched weather data for {self.station_icao}")
        else:
            logger.warning(f"Failed to parse METAR data for {self.station_icao}")
        return weather_data

    async def _do_fetch_metar(self) -> dict[str, Any] | None:
        """One attempt at hitting the METAR endpoint.

        Returns the first (most recent) report dict on success, or None
        when the upstream replied 200 with an empty list (no data is not
        a transient failure - retrying will not help, and the outer
        wrapper will handle it). Raises on transient errors so the retry
        helper can re-attempt.
        """
        url = f"{self.api_url}?ids={self.station_icao}&format=json"
        logger.debug(f"Fetching METAR from {url}")

        response = await self.client.get(url)
        # Promote 5xx to a retryable type; let 4xx fall through as
        # HTTPStatusError so the outer except logs it and returns None.
        if 500 <= response.status_code < 600:
            raise _TransientHTTPError(response.status_code, url)
        response.raise_for_status()

        data = response.json()
        if not data or not isinstance(data, list) or len(data) == 0:
            logger.warning(f"No METAR data returned for {self.station_icao}")
            return None
        return data[0]

    def _parse_metar(self, metar_report: dict[str, Any]) -> dict[str, Any] | None:
        """Parse METAR report into structured weather data.

        Args:
            metar_report: METAR report dictionary from API

        Returns:
            Dictionary with weather parameters, or None if parsing failed
        """
        try:
            weather_data = {}

            # Extract raw METAR text
            raw_text = metar_report.get("rawOb", "")
            if not raw_text:
                logger.warning("No raw METAR text found in response")
                return None

            # Parse temperature (in Celsius)
            temp_c = metar_report.get("temp")
            if temp_c is not None:
                weather_data["temperature"] = float(temp_c)
            else:
                logger.warning("Temperature not found in METAR")

            # Parse dewpoint (in Celsius)
            dewp_c = metar_report.get("dewp")
            if dewp_c is not None:
                weather_data["dewpoint"] = float(dewp_c)
            else:
                logger.warning("Dewpoint not found in METAR")

            # Calculate relative humidity from temperature and dewpoint
            if "temperature" in weather_data and "dewpoint" in weather_data:
                rh = self._calculate_rh(weather_data["temperature"], weather_data["dewpoint"])
                weather_data["relative_humidity"] = rh

            # Parse pressure (already in hPa from aviationweather.gov)
            altim = metar_report.get("altim")
            if altim is not None:
                # aviationweather.gov returns hPa directly (e.g., 1033.3)
                # No conversion needed
                pressure_hpa = float(altim)
                weather_data["pressure"] = round(pressure_hpa, 2)
            else:
                logger.warning("Pressure not found in METAR")

            # Parse wind speed and direction
            wdir = metar_report.get("wdir")
            wspd = metar_report.get("wspd")

            if wdir is not None:
                try:
                    weather_data["wind_direction"] = int(wdir)
                except ValueError:
                    if str(wdir).upper() != "VRB":
                        logger.warning(f"Invalid wind direction value: {wdir}")
            else:
                logger.warning("Wind direction not found in METAR")

            if wspd is not None:
                # Wind speed is in knots, convert to m/s
                # 1 knot = 0.514444 m/s
                wind_speed_ms = float(wspd) * 0.514444
                weather_data["wind_speed"] = round(wind_speed_ms, 2)
            else:
                logger.warning("Wind speed not found in METAR")

            # Parse precipitation (if available)
            # METAR may include precipitation in remarks or wxString
            wx_string = metar_report.get("wxString", "")
            precip = metar_report.get("precip")

            if precip is not None:
                # Precipitation in inches, convert to mm
                # 1 inch = 25.4 mm
                precip_mm = float(precip) * 25.4
                weather_data["precipitation"] = round(precip_mm, 2)
            elif "RA" in wx_string or "SN" in wx_string or "DZ" in wx_string:
                # Indicates precipitation but no amount
                weather_data["precipitation"] = 0.0
            else:
                # No precipitation data available
                weather_data["precipitation"] = None

            # Add timestamp.
            #
            # aviationweather.gov returns obsTime in *two* shapes depending on
            # the endpoint version: an ISO-8601 string (e.g.
            # "2024-01-15T10:30:00Z") OR a Unix epoch integer in seconds
            # (e.g. 1776600420). The original code only handled ISO and fell
            # through to datetime.now() for the int form, which silently
            # dropped the real observation time.
            obs_time = metar_report.get("obsTime")
            if obs_time is not None and obs_time != "":
                try:
                    if isinstance(obs_time, (int, float)) or (
                        isinstance(obs_time, str) and obs_time.isdigit()
                    ):
                        epoch = int(obs_time)
                        # Defensive sanity bound: anything before 2010 or
                        # >1 day in the future is almost certainly a parse
                        # mistake (e.g. milliseconds vs seconds).
                        if 1_262_304_000 <= epoch <= int(datetime.now().timestamp()) + 86_400:
                            weather_data["timestamp"] = datetime.fromtimestamp(epoch)
                        else:
                            logger.warning(
                                f"obsTime epoch {epoch} outside sane window, "
                                f"using current time"
                            )
                            weather_data["timestamp"] = datetime.now()
                    else:
                        time_str = str(obs_time)
                        if time_str.endswith("Z"):
                            time_str = time_str.replace("Z", "+00:00")
                        weather_data["timestamp"] = datetime.fromisoformat(time_str)
                except Exception as e:
                    logger.warning(f"Failed to parse timestamp {obs_time}: {e}, using current time")
                    weather_data["timestamp"] = datetime.now()
            else:
                weather_data["timestamp"] = datetime.now()

            return weather_data

        except Exception as e:
            logger.error(f"Error parsing METAR data: {e}", exc_info=True)
            return None

    def _calculate_rh(self, temp_c: float, dewpoint_c: float) -> float:
        """Calculate relative humidity from temperature and dewpoint.

        Thin wrapper around ``shared.calculate_rh_from_dewpoint`` so the
        existing call site at line 168 keeps working without re-routing.
        Phase 6 lifted the math into ``shared/climate.py``; this method
        is preserved as the public surface for the WeatherClient class.
        Numerical delta from the previous local impl (Magnus b=237.7 vs
        canonical b=237.3) is < 0.05 % RH at all temperatures we see —
        well under METAR's reporting precision (1 °C / integer % RH).
        """
        try:
            return round(calculate_rh_from_dewpoint(temp_c, dewpoint_c), 2)
        except Exception as e:
            logger.error(f"Error calculating RH: {e}")
            return 0.0
