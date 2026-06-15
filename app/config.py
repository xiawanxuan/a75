import yaml
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

from pydantic_settings import BaseSettings


class ServerConfig(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 8
    max_concurrent_requests: int = 1000


class DatabaseConfig(BaseSettings):
    host: str = "localhost"
    port: int
    user: str
    password: str
    database: str
    max_connections: int = 100
    min_connections: int = 5
    command_timeout: int = 60000


class TimescaleDBConfig(DatabaseConfig):
    port: int = 5432


class MySQLConfig(DatabaseConfig):
    port: int = 3306


class OrderResamplingConfig(BaseSettings):
    interpolation_method: str = "cubic"
    resample_points_per_order: int = 256
    samples_per_order: int = 256
    max_order: float = 20.0
    min_order: float = 0.5
    anti_aliasing_filter: bool = True
    filter_order: int = 8
    cutoff_ratio: float = 0.45


class SpectralDecompositionConfig(BaseSettings):
    fft_window: str = "hann"
    fft_overlap: float = 0.75
    overlap_ratio: float = 0.75
    fft_nperseg: int = 4096
    harmonic_count: int = 5
    sideband_range: int = 5
    peak_threshold: float = 0.1
    peak_prominence: float = 0.05
    peak_distance_orders: float = 0.1
    noise_floor_percentile: float = 95
    snr_threshold_db: float = 3.0
    sideband_tolerance: float = 0.05


class FatigueDamageConfig(BaseSettings):
    sn_curve_slope: float = 5.0
    sn_curve_intercept: float = 1500.0
    fatigue_limit_stress: float = 50.0
    mean_stress_correction: str = "goodman"
    ultimate_tensile_strength: float = 800.0
    yield_strength: float = 650.0
    rainflow_bins: int = 64
    damage_threshold: float = 1e-6


class IngestionConfig(BaseSettings):
    max_shard_size_mb: int = 32
    max_concurrent_writers: int = 512
    batch_size: int = 1024
    compression_enabled: bool = True
    retention_days: int = 3650
    backup_failed_data: bool = True
    backup_directory: str = "./data/failed_backups"


class LoggingConfig(BaseSettings):
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_file: str = "./logs/app.log"
    max_file_size_mb: int = 100
    backup_count: int = 10


class OpenAPIConfig(BaseSettings):
    title: str
    version: str
    description: str
    contact: Dict[str, str]


class AppConfig(BaseSettings):
    server: ServerConfig
    timescaledb: TimescaleDBConfig
    mysql: MySQLConfig
    order_resampling: OrderResamplingConfig
    spectral_decomposition: SpectralDecompositionConfig
    fatigue_damage: FatigueDamageConfig
    ingestion: IngestionConfig
    logging: LoggingConfig
    openapi: OpenAPIConfig

    class Config:
        env_prefix = "TURBINE_"


@lru_cache(maxsize=1)
def load_config(config_path: str = None) -> AppConfig:
    if config_path is None:
        config_path = Path(__file__).parent / "config.yaml"

    with open(config_path, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)

    return AppConfig(
        server=ServerConfig(**raw_config["server"]),
        timescaledb=TimescaleDBConfig(**raw_config["database"]["timescaledb"]),
        mysql=MySQLConfig(**raw_config["database"]["mysql"]),
        order_resampling=OrderResamplingConfig(
            **raw_config["algorithm"]["order_resampling"]
        ),
        spectral_decomposition=SpectralDecompositionConfig(
            **raw_config["algorithm"]["spectral_decomposition"]
        ),
        fatigue_damage=FatigueDamageConfig(
            **raw_config["algorithm"]["fatigue_damage"]
        ),
        ingestion=IngestionConfig(**raw_config["ingestion"]),
        logging=LoggingConfig(**raw_config["logging"]),
        openapi=OpenAPIConfig(**raw_config["openapi"]),
    )


config = load_config()
