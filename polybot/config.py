"""Configuration management using Pydantic Settings."""

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class PolymarketConfig(BaseSettings):
    """Polymarket-specific configuration."""

    model_config = SettingsConfigDict(env_prefix="POLYMARKET_")

    private_key: SecretStr = Field(..., description="Polygon wallet private key")
    funder_address: str = Field(..., description="Polygon wallet address")
    api_key: Optional[str] = Field(None, description="Optional CLOB API key")
    api_secret: Optional[SecretStr] = Field(None, description="Optional CLOB API secret")
    api_passphrase: Optional[SecretStr] = Field(None, description="Optional API passphrase")

    # Endpoints
    clob_url: str = "https://clob.polymarket.com"
    gamma_url: str = "https://gamma-api.polymarket.com"
    ws_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

    # Chain config
    chain_id: int = 137  # Polygon Mainnet
    rpc_url: str = "https://polygon-rpc.com"


class PythConfig(BaseSettings):
    """Pyth Network configuration."""

    model_config = SettingsConfigDict(env_prefix="PYTH_")

    hermes_url: str = "https://hermes.pyth.network"
    poll_interval_ms: int = 400

    # Price feed IDs (hex format)
    xrp_usd_feed: str = "0xec5d399846a9209f3fe5881d70aae9268c94339ff9817e8d18ff19fa05eea1c8"
    btc_usd_feed: str = "0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43"
    eth_usd_feed: str = "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace"


class RiskConfig(BaseSettings):
    """Risk management configuration."""

    model_config = SettingsConfigDict(env_prefix="")

    max_position_percent: float = Field(5.0, description="Max % of capital per trade")
    min_arb_profit_percent: float = Field(1.5, description="Min profit to execute arb")
    max_slippage_percent: float = Field(2.0, description="Max allowed slippage")
    max_open_positions: int = Field(20, description="Max concurrent positions")
    circuit_breaker_loss_percent: float = Field(10.0, description="Stop trading if daily loss exceeds")


class WeatherConfig(BaseSettings):
    """Weather strategy v2 configuration."""

    model_config = SettingsConfigDict(env_prefix="WEATHER_")

    min_edge_percent: float = Field(10.0, description="Min edge % to trigger signal")
    min_zscore: float = Field(1.5, description="Min statistical significance")
    max_kelly_fraction: float = Field(0.25, description="Cap on Kelly bet sizing")
    enabled: bool = Field(True, description="Enable weather strategy")


class CryptoConfig(BaseSettings):
    """Crypto price prediction strategy configuration."""

    model_config = SettingsConfigDict(env_prefix="CRYPTO_")

    min_edge_percent: float = Field(8.0, description="Min edge % to trigger signal")
    min_zscore: float = Field(1.5, description="Min statistical significance")
    min_days_to_expiry: float = Field(0.5, description="Min 12 hours to expiry")
    max_days_to_expiry: float = Field(30.0, description="Max 30 days to expiry")
    enabled: bool = Field(True, description="Enable crypto strategy")


class CopyTradingConfig(BaseSettings):
    """Copy trading configuration."""

    model_config = SettingsConfigDict(env_prefix="COPY_")

    copy_delay_seconds: float = Field(45.0, description="Delay before copying trade")
    max_copy_size: float = Field(100.0, description="Max USD per copy trade")
    copy_fraction: float = Field(0.10, description="Copy 10% of original size")
    min_trader_win_rate: float = Field(0.60, description="Min win rate to copy")
    poll_interval_seconds: float = Field(15.0, description="How often to check for trades")
    enabled: bool = Field(False, description="Enable copy trading (requires setup)")


class DashboardConfig(BaseSettings):
    """Dashboard configuration."""

    model_config = SettingsConfigDict(env_prefix="DASHBOARD_")

    port: int = 8501
    host: str = "0.0.0.0"


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Sub-configs
    polymarket: PolymarketConfig = Field(default_factory=PolymarketConfig)
    pyth: PythConfig = Field(default_factory=PythConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    weather: WeatherConfig = Field(default_factory=WeatherConfig)
    crypto: CryptoConfig = Field(default_factory=CryptoConfig)
    copy_trading: CopyTradingConfig = Field(default_factory=CopyTradingConfig)

    # Logging
    log_level: str = "INFO"
    log_file: Path = Path("logs/polybot.log")

    # Data storage
    data_dir: Path = Path("data")
    db_path: Path = Path("data/polybot.db")


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
