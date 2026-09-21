"""Application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env from repo root or backend cwd
_ROOT = Path(__file__).resolve().parents[3]
_ENV_CANDIDATES = (
    _ROOT / ".env",
    Path.cwd() / ".env",
    Path.cwd().parent / ".env",
)
_ENV_FILE = next((p for p in _ENV_CANDIDATES if p.is_file()), ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Application
    app_name: str = "CryptoSignal"
    app_env: str = "development"
    debug: bool = True
    log_level: str = "INFO"
    secret_key: str = "change-me"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Database
    database_url: str = "postgresql+asyncpg://crypto:crypto@localhost:5432/crypto_signals"
    database_url_sync: str = "postgresql://crypto:crypto@localhost:5432/crypto_signals"

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_cache_ttl: int = 300

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Analysis
    default_symbols: str = "BTC,ETH,SOL,BNB,XRP,ADA,DOGE,AVAX,DOT,LINK,MATIC,UNI,ATOM,LTC,NEAR"
    analysis_timeframes: str = "1h,4h,1d,1w"
    signal_schedule_cron: str = "0 8 * * *"
    # Local storage + universe size
    data_dir: str = ""  # empty → <repo>/data
    fetch_all_coins: bool = True
    coingecko_market_pages: int = 4  # 250 coins/page → up to 1000
    signal_universe_size: int = 50  # top N by volume for signal analysis
    persist_ohlcv: bool = True
    analysis_timeframes_for_signals: str = "1d,4h"  # lighter set when scanning many coins

    # Market APIs
    coingecko_api_key: str = ""
    coingecko_base_url: str = "https://api.coingecko.com/api/v3"
    coinmarketcap_api_key: str = ""
    coinmarketcap_base_url: str = "https://pro-api.coinmarketcap.com/v1"
    binance_api_key: str = ""
    binance_api_secret: str = ""
    binance_base_url: str = "https://api.binance.com"
    kraken_api_key: str = ""
    kraken_api_secret: str = ""
    kraken_base_url: str = "https://api.kraken.com"
    kucoin_api_key: str = ""
    kucoin_api_secret: str = ""
    kucoin_api_passphrase: str = ""
    kucoin_base_url: str = "https://api.kucoin.com"
    yahoo_finance_enabled: bool = True

    # Sentiment
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "CryptoSignalBot/1.0"
    twitter_bearer_token: str = ""
    twitter_api_key: str = ""
    twitter_api_secret: str = ""
    lunarcrush_api_key: str = ""
    lunarcrush_base_url: str = "https://lunarcrush.com/api4"
    santiment_api_key: str = ""
    santiment_base_url: str = "https://api.santiment.net"
    google_trends_enabled: bool = True
    fear_greed_base_url: str = "https://api.alternative.me/fng"

    # On-chain
    glassnode_api_key: str = ""
    glassnode_base_url: str = "https://api.glassnode.com/v1"
    covalent_api_key: str = ""
    covalent_base_url: str = "https://api.covalenthq.com/v1"
    dune_api_key: str = ""
    dune_base_url: str = "https://api.dune.com/api/v1"
    arkham_api_key: str = ""
    arkham_base_url: str = "https://api.arkhamintelligence.com"
    etherscan_api_key: str = ""
    etherscan_base_url: str = "https://api.etherscan.io/api"
    blockchair_api_key: str = ""
    blockchair_base_url: str = "https://api.blockchair.com"

    # News & macro
    newsapi_key: str = ""
    newsapi_base_url: str = "https://newsapi.org/v2"
    cryptopanic_api_key: str = ""
    cryptopanic_base_url: str = "https://cryptopanic.com/api/v1"
    finnhub_api_key: str = ""
    finnhub_base_url: str = "https://finnhub.io/api/v1"
    fred_api_key: str = ""
    fred_base_url: str = "https://api.stlouisfed.org/fred"

    # Alternative
    github_token: str = ""
    github_base_url: str = "https://api.github.com"
    defillama_base_url: str = "https://api.llama.fi"

    # Alerts
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    alert_email_to: str = ""

    # Signal weights (must sum to 1.0)
    weight_technical: float = Field(default=0.35, ge=0, le=1)
    weight_sentiment: float = Field(default=0.15, ge=0, le=1)
    weight_onchain: float = Field(default=0.15, ge=0, le=1)
    weight_volume: float = Field(default=0.10, ge=0, le=1)
    weight_macro: float = Field(default=0.10, ge=0, le=1)
    weight_ml: float = Field(default=0.15, ge=0, le=1)

    @model_validator(mode="after")
    def _check_weight_sum(self) -> "Settings":
        total = (
            self.weight_technical
            + self.weight_sentiment
            + self.weight_onchain
            + self.weight_volume
            + self.weight_macro
            + self.weight_ml
        )
        if not (0.99 <= total <= 1.01):
            raise ValueError(f"Signal weights must sum to 1.0, got {total:.3f}")
        return self

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def symbol_list(self) -> List[str]:
        return [s.strip().upper() for s in self.default_symbols.split(",") if s.strip()]

    @property
    def timeframe_list(self) -> List[str]:
        return [t.strip() for t in self.analysis_timeframes.split(",") if t.strip()]

    @property
    def signal_timeframe_list(self) -> List[str]:
        raw = self.analysis_timeframes_for_signals or self.analysis_timeframes
        return [t.strip() for t in raw.split(",") if t.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
