-- =======================================================
-- 燃气电厂机组健康诊断平台 - TimescaleDB 初始化脚本
-- 功能：存储高频应变时序波形、阶次分析结果、疲劳损伤特征
-- =======================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit;

-- =======================================================
-- 1. 高频应变波形原始数据表
-- =======================================================
CREATE TABLE IF NOT EXISTS strain_waveforms (
    time              TIMESTAMPTZ       NOT NULL,
    unit_id           VARCHAR(32)       NOT NULL,
    blade_id          VARCHAR(32)       NOT NULL,
    channel_id        SMALLINT          NOT NULL,
    sample_rate       INTEGER           NOT NULL,
    rpm               DOUBLE PRECISION  NOT NULL,
    strain_values     REAL[]            NOT NULL,
    sample_count      INTEGER           NOT NULL,
    shard_id          VARCHAR(64)       NOT NULL,
    upload_id         VARCHAR(64)       NOT NULL,
    created_at        TIMESTAMPTZ       DEFAULT NOW(),
    PRIMARY KEY (time, unit_id, blade_id, channel_id)
);

SELECT create_hypertable(
    'strain_waveforms',
    'time',
    chunk_time_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_strain_waveforms_unit_time
    ON strain_waveforms (unit_id, time DESC);

CREATE INDEX IF NOT EXISTS idx_strain_waveforms_upload
    ON strain_waveforms (upload_id);

ALTER TABLE strain_waveforms SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'unit_id, blade_id',
    timescaledb.compress_orderby = 'time DESC'
);

SELECT add_compression_policy(
    'strain_waveforms',
    INTERVAL '7 days',
    if_not_exists => TRUE
);

SELECT add_retention_policy(
    'strain_waveforms',
    INTERVAL '10 years',
    if_not_exists => TRUE
);

-- =======================================================
-- 2. 转速同步阶次重采样结果表
-- =======================================================
CREATE TABLE IF NOT EXISTS order_resampled_waveforms (
    time              TIMESTAMPTZ       NOT NULL,
    unit_id           VARCHAR(32)       NOT NULL,
    blade_id          VARCHAR(32)       NOT NULL,
    channel_id        SMALLINT          NOT NULL,
    base_order        DOUBLE PRECISION  NOT NULL,
    order_values      REAL[]            NOT NULL,
    amplitude_values  REAL[]            NOT NULL,
    phase_values      REAL[]            NOT NULL,
    rpm_range         DOUBLE PRECISION[] NOT NULL,
    analysis_window   INTERVAL          NOT NULL,
    upload_id         VARCHAR(64)       NOT NULL,
    created_at        TIMESTAMPTZ       DEFAULT NOW(),
    PRIMARY KEY (time, unit_id, blade_id, channel_id)
);

SELECT create_hypertable(
    'order_resampled_waveforms',
    'time',
    chunk_time_interval => INTERVAL '4 hours',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_order_resampled_unit_blade
    ON order_resampled_waveforms (unit_id, blade_id, time DESC);

ALTER TABLE order_resampled_waveforms SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'unit_id, blade_id',
    timescaledb.compress_orderby = 'time DESC'
);

-- =======================================================
-- 3. 阶次谱分解结果表
-- =======================================================
CREATE TABLE IF NOT EXISTS order_spectrum (
    time              TIMESTAMPTZ       NOT NULL,
    unit_id           VARCHAR(32)       NOT NULL,
    blade_id          VARCHAR(32)       NOT NULL,
    channel_id        SMALLINT          NOT NULL,
    resonance_orders  DOUBLE PRECISION[] NOT NULL,
    resonance_amplitudes REAL[]         NOT NULL,
    harmonic_orders   DOUBLE PRECISION[] NOT NULL,
    harmonic_amplitudes REAL[]          NOT NULL,
    sideband_orders   DOUBLE PRECISION[] NOT NULL,
    sideband_amplitudes REAL[]          NOT NULL,
    noise_floor       DOUBLE PRECISION  NOT NULL,
    snr               DOUBLE PRECISION  NOT NULL,
    upload_id         VARCHAR(64)       NOT NULL,
    created_at        TIMESTAMPTZ       DEFAULT NOW(),
    PRIMARY KEY (time, unit_id, blade_id, channel_id)
);

SELECT create_hypertable(
    'order_spectrum',
    'time',
    chunk_time_interval => INTERVAL '4 hours',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_order_spectrum_unit_time
    ON order_spectrum (unit_id, time DESC);

-- =======================================================
-- 4. 疲劳损伤计算结果表
-- =======================================================
CREATE TABLE IF NOT EXISTS fatigue_damage (
    time              TIMESTAMPTZ       NOT NULL,
    unit_id           VARCHAR(32)       NOT NULL,
    blade_id          VARCHAR(32)       NOT NULL,
    channel_id        SMALLINT          NOT NULL,
    damage_value      DOUBLE PRECISION  NOT NULL,
    remaining_life    DOUBLE PRECISION  NOT NULL,
    cycle_count       INTEGER           NOT NULL,
    max_stress        DOUBLE PRECISION  NOT NULL,
    min_stress        DOUBLE PRECISION  NOT NULL,
    mean_stress       DOUBLE PRECISION  NOT NULL,
    stress_amplitude  DOUBLE PRECISION  NOT NULL,
    damage_accumulated DOUBLE PRECISION NOT NULL,
    upload_id         VARCHAR(64)       NOT NULL,
    created_at        TIMESTAMPTZ       DEFAULT NOW(),
    PRIMARY KEY (time, unit_id, blade_id, channel_id)
);

SELECT create_hypertable(
    'fatigue_damage',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_fatigue_damage_unit_blade
    ON fatigue_damage (unit_id, blade_id, time DESC);

-- =======================================================
-- 5. 分析失败数据留档表
-- =======================================================
CREATE TABLE IF NOT EXISTS analysis_failures (
    failure_id        BIGSERIAL PRIMARY KEY,
    time              TIMESTAMPTZ       NOT NULL,
    unit_id           VARCHAR(32)       NOT NULL,
    blade_id          VARCHAR(32)       NOT NULL,
    upload_id         VARCHAR(64)       NOT NULL,
    error_code        INTEGER           NOT NULL,
    error_message     TEXT              NOT NULL,
    stack_trace       TEXT,
    raw_strain_data   BYTEA,
    raw_rpm_data      BYTEA,
    algorithm_params  JSONB,
    retry_count       INTEGER           DEFAULT 0,
    last_retry_time   TIMESTAMPTZ,
    resolved          BOOLEAN           DEFAULT FALSE,
    resolved_at       TIMESTAMPTZ,
    created_at        TIMESTAMPTZ       DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_analysis_failures_unit
    ON analysis_failures (unit_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_analysis_failures_upload
    ON analysis_failures (upload_id);

CREATE INDEX IF NOT EXISTS idx_analysis_failures_unit
    ON analysis_failures (unit_id, created_at DESC);

-- =======================================================
-- 6. HTTP 回调推送记录表
-- =======================================================
CREATE TABLE IF NOT EXISTS callback_push_records (
    record_id           BIGSERIAL PRIMARY KEY,
    event_id            VARCHAR(64)       NOT NULL,
    time                TIMESTAMPTZ       NOT NULL,
    unit_id             VARCHAR(32)       NOT NULL,
    blade_id            VARCHAR(32)       NOT NULL,
    channel_id          INTEGER           NOT NULL,
    target_name         VARCHAR(64)       NOT NULL,
    target_url          VARCHAR(512)      NOT NULL,
    success             BOOLEAN           NOT NULL,
    status_code         INTEGER,
    response_text       TEXT,
    error_message       TEXT,
    retry_count         INTEGER           DEFAULT 0,
    payload             JSONB,
    resonance_orders    DOUBLE PRECISION[],
    resonance_amplitudes DOUBLE PRECISION[],
    max_damage          DOUBLE PRECISION,
    avg_rpm             DOUBLE PRECISION,
    created_at          TIMESTAMPTZ       DEFAULT NOW(),
    updated_at          TIMESTAMPTZ       DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_callback_push_records_event
    ON callback_push_records (event_id, target_name);

CREATE INDEX IF NOT EXISTS idx_callback_push_records_unit_time
    ON callback_push_records (unit_id, time DESC);

CREATE INDEX IF NOT EXISTS idx_callback_push_records_success
    ON callback_push_records (success, time DESC);

SELECT create_hypertable(
    'callback_push_records',
    'time',
    if_not_exists => TRUE
);

SELECT add_compression_policy(
    'callback_push_records',
    INTERVAL '90 days',
    if_not_exists => TRUE
);

SELECT add_retention_policy(
    'callback_push_records',
    INTERVAL '365 days',
    if_not_exists => TRUE
);

-- =======================================================
-- 7. 连续聚合视图 - 小时级损伤汇总
-- =======================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS fatigue_damage_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    unit_id,
    blade_id,
    AVG(damage_value) AS avg_damage,
    MAX(damage_value) AS max_damage,
    SUM(damage_value) AS total_damage,
    AVG(remaining_life) AS avg_remaining_life,
    MAX(max_stress) AS max_stress,
    COUNT(*) AS sample_count
FROM fatigue_damage
GROUP BY bucket, unit_id, blade_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'fatigue_damage_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- =======================================================
-- 8. 连续聚合视图 - 阶次特征小时级汇总
-- =======================================================
CREATE MATERIALIZED VIEW IF NOT EXISTS order_spectrum_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    unit_id,
    blade_id,
    AVG(snr) AS avg_snr,
    MAX(resonance_amplitudes[1]) AS max_resonance_amplitude,
    AVG(noise_floor) AS avg_noise_floor,
    COUNT(*) AS sample_count
FROM order_spectrum
GROUP BY bucket, unit_id, blade_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'order_spectrum_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- =======================================================
-- 9. 数据压缩策略
-- =======================================================
SELECT add_compression_policy(
    'order_spectrum',
    INTERVAL '30 days',
    if_not_exists => TRUE
);

SELECT add_compression_policy(
    'fatigue_damage',
    INTERVAL '30 days',
    if_not_exists => TRUE
);

SELECT add_compression_policy(
    'order_resampled_waveforms',
    INTERVAL '30 days',
    if_not_exists => TRUE
);
