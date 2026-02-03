"""
NOAA Weather API Connector

Fetches official weather forecasts from the National Weather Service.
Used to identify mispriced weather prediction markets.

API Docs: https://www.weather.gov/documentation/services-web-api
"""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

import httpx
from loguru import logger
from pydantic import BaseModel, Field


class WeatherForecast(BaseModel):
    """Weather forecast for a specific time period."""

    location: str
    start_time: datetime
    end_time: datetime
    temperature: int  # Fahrenheit
    temperature_unit: str = "F"
    precipitation_probability: int  # 0-100
    wind_speed: str
    wind_direction: str
    short_forecast: str
    detailed_forecast: str
    is_daytime: bool

    @property
    def precip_decimal(self) -> Decimal:
        """Precipitation probability as decimal (0-1)."""
        return Decimal(self.precipitation_probability) / 100


class DailyForecast(BaseModel):
    """Aggregated daily forecast."""

    location: str
    date: str
    high_temp: int
    low_temp: int
    precipitation_probability: int
    conditions: str
    
    @property
    def precip_decimal(self) -> Decimal:
        return Decimal(self.precipitation_probability) / 100


class NOAAConnector:
    """
    Connector for NOAA Weather API.
    
    Usage:
        noaa = NOAAConnector()
        await noaa.connect()
        forecast = await noaa.get_forecast("New York, NY")
    """

    # Major city coordinates
    CITIES = {
        "New York": (40.7128, -74.0060),
        "Los Angeles": (34.0522, -118.2437),
        "Chicago": (41.8781, -87.6298),
        "Houston": (29.7604, -95.3698),
        "Phoenix": (33.4484, -112.0740),
        "Philadelphia": (39.9526, -75.1652),
        "San Antonio": (29.4241, -98.4936),
        "San Diego": (32.7157, -117.1611),
        "Dallas": (32.7767, -96.7970),
        "Miami": (25.7617, -80.1918),
        "Denver": (39.7392, -104.9903),
        "Seattle": (47.6062, -122.3321),
        "Boston": (42.3601, -71.0589),
        "Atlanta": (33.7490, -84.3880),
        "Las Vegas": (36.1699, -115.1398),
        "Washington DC": (38.9072, -77.0369),
        "Detroit": (42.3314, -83.0458),
        "Minneapolis": (44.9778, -93.2650),
        "San Francisco": (37.7749, -122.4194),
        "Tampa": (27.9506, -82.4572),
    }

    def __init__(self, user_agent: str = "PolyBot/1.0 (weather@polybot.local)"):
        self.base_url = "https://api.weather.gov"
        self.user_agent = user_agent
        self._http: Optional[httpx.AsyncClient] = None
        self._grid_cache: dict[str, tuple[str, int, int]] = {}  # city -> (office, x, y)

    async def connect(self) -> None:
        """Initialize HTTP client."""
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=30.0,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/geo+json",
            },
        )
        logger.info("NOAA Weather connector initialized")

    async def disconnect(self) -> None:
        """Close HTTP client."""
        if self._http:
            await self._http.aclose()
        logger.info("NOAA Weather connector disconnected")

    async def _get_grid_point(self, city: str) -> tuple[str, int, int]:
        """Get grid point (office, x, y) for a city."""
        if city in self._grid_cache:
            return self._grid_cache[city]

        if city not in self.CITIES:
            raise ValueError(f"Unknown city: {city}. Available: {list(self.CITIES.keys())}")

        lat, lon = self.CITIES[city]
        
        try:
            resp = await self._http.get(f"/points/{lat},{lon}")
            resp.raise_for_status()
            data = resp.json()
            
            props = data["properties"]
            office = props["gridId"]
            x = props["gridX"]
            y = props["gridY"]
            
            self._grid_cache[city] = (office, x, y)
            return office, x, y
            
        except Exception as e:
            logger.error(f"Failed to get grid point for {city}: {e}")
            raise

    async def get_forecast(self, city: str) -> list[WeatherForecast]:
        """
        Get detailed forecast for a city.
        
        Returns 12-hour periods for the next 7 days.
        """
        office, x, y = await self._get_grid_point(city)
        
        try:
            resp = await self._http.get(f"/gridpoints/{office}/{x},{y}/forecast")
            resp.raise_for_status()
            data = resp.json()
            
            forecasts = []
            for period in data["properties"]["periods"]:
                forecast = WeatherForecast(
                    location=city,
                    start_time=datetime.fromisoformat(period["startTime"].replace("Z", "+00:00")),
                    end_time=datetime.fromisoformat(period["endTime"].replace("Z", "+00:00")),
                    temperature=period["temperature"],
                    temperature_unit=period["temperatureUnit"],
                    precipitation_probability=period.get("probabilityOfPrecipitation", {}).get("value") or 0,
                    wind_speed=period["windSpeed"],
                    wind_direction=period["windDirection"],
                    short_forecast=period["shortForecast"],
                    detailed_forecast=period["detailedForecast"],
                    is_daytime=period["isDaytime"],
                )
                forecasts.append(forecast)
            
            logger.debug(f"Got {len(forecasts)} forecast periods for {city}")
            return forecasts
            
        except Exception as e:
            logger.error(f"Failed to get forecast for {city}: {e}")
            raise

    async def get_hourly_forecast(self, city: str) -> list[WeatherForecast]:
        """Get hourly forecast for the next 7 days."""
        office, x, y = await self._get_grid_point(city)
        
        try:
            resp = await self._http.get(f"/gridpoints/{office}/{x},{y}/forecast/hourly")
            resp.raise_for_status()
            data = resp.json()
            
            forecasts = []
            for period in data["properties"]["periods"]:
                forecast = WeatherForecast(
                    location=city,
                    start_time=datetime.fromisoformat(period["startTime"].replace("Z", "+00:00")),
                    end_time=datetime.fromisoformat(period["endTime"].replace("Z", "+00:00")),
                    temperature=period["temperature"],
                    temperature_unit=period["temperatureUnit"],
                    precipitation_probability=period.get("probabilityOfPrecipitation", {}).get("value") or 0,
                    wind_speed=period["windSpeed"],
                    wind_direction=period["windDirection"],
                    short_forecast=period["shortForecast"],
                    detailed_forecast=period.get("detailedForecast", ""),
                    is_daytime=period.get("isDaytime", True),
                )
                forecasts.append(forecast)
            
            return forecasts
            
        except Exception as e:
            logger.error(f"Failed to get hourly forecast for {city}: {e}")
            raise

    async def get_daily_summary(self, city: str) -> list[DailyForecast]:
        """Get daily forecast summary for the next 7 days."""
        forecasts = await self.get_forecast(city)
        
        # Group by date
        daily: dict[str, dict] = {}
        
        for f in forecasts:
            date = f.start_time.strftime("%Y-%m-%d")
            if date not in daily:
                daily[date] = {
                    "location": city,
                    "date": date,
                    "high_temp": f.temperature if f.is_daytime else -999,
                    "low_temp": f.temperature if not f.is_daytime else 999,
                    "precipitation_probability": f.precipitation_probability,
                    "conditions": f.short_forecast if f.is_daytime else "",
                }
            else:
                if f.is_daytime:
                    daily[date]["high_temp"] = max(daily[date]["high_temp"], f.temperature)
                    if not daily[date]["conditions"]:
                        daily[date]["conditions"] = f.short_forecast
                else:
                    daily[date]["low_temp"] = min(daily[date]["low_temp"], f.temperature)
                daily[date]["precipitation_probability"] = max(
                    daily[date]["precipitation_probability"],
                    f.precipitation_probability,
                )
        
        # Convert to models
        result = []
        for d in daily.values():
            if d["high_temp"] == -999:
                d["high_temp"] = d["low_temp"] + 15  # Estimate
            if d["low_temp"] == 999:
                d["low_temp"] = d["high_temp"] - 15
            result.append(DailyForecast(**d))
        
        return result

    async def will_it_rain(self, city: str, date: Optional[str] = None) -> tuple[bool, int]:
        """
        Check if it will rain in a city.
        
        Returns: (likely_rain, probability_percent)
        """
        daily = await self.get_daily_summary(city)
        
        if date:
            for d in daily:
                if d.date == date:
                    return d.precipitation_probability >= 50, d.precipitation_probability
            return False, 0
        
        # Check tomorrow
        tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
        for d in daily:
            if d.date == tomorrow:
                return d.precipitation_probability >= 50, d.precipitation_probability
        
        return False, 0

    async def will_it_snow(self, city: str, date: Optional[str] = None) -> tuple[bool, int]:
        """
        Check if it will snow in a city.
        
        Returns: (likely_snow, confidence_percent)
        """
        forecasts = await self.get_forecast(city)
        
        target_date = date or (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
        
        for f in forecasts:
            if f.start_time.strftime("%Y-%m-%d") == target_date:
                # Check for snow keywords
                snow_keywords = ["snow", "flurries", "blizzard", "wintry mix"]
                forecast_lower = f.short_forecast.lower()
                
                for keyword in snow_keywords:
                    if keyword in forecast_lower:
                        # Also check temperature (must be cold enough)
                        if f.temperature <= 35:
                            return True, f.precipitation_probability
        
        return False, 0

    async def get_temperature_forecast(
        self, city: str, date: str
    ) -> tuple[Optional[int], Optional[int]]:
        """
        Get high/low temperature forecast for a date.
        
        Returns: (high, low) in Fahrenheit
        """
        daily = await self.get_daily_summary(city)
        
        for d in daily:
            if d.date == date:
                return d.high_temp, d.low_temp
        
        return None, None
