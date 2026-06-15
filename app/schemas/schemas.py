from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class ResponseStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


class ApiResponse(BaseModel):
    code: int = Field(..., description="响应码，0表示成功，非0表示错误")
    message: str = Field(..., description="响应消息")
    status: ResponseStatus = Field(..., description="响应状态")
    data: Optional[Any] = Field(None, description="响应数据")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="响应时间戳")
    request_id: Optional[str] = Field(None, description="请求ID")

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class UnitInfo(BaseModel):
    unit_id: str = Field(..., description="机组ID")
    unit_name: str = Field(..., description="机组名称")
    plant_id: str = Field(..., description="电厂ID")
    plant_name: str = Field(..., description="电厂名称")
    unit_type: str = Field(..., description="机组类型")
    capacity_mw: float = Field(..., description="装机容量(MW)")
    rated_rpm: int = Field(..., description="额定转速(RPM)")
    min_rpm: Optional[int] = Field(None, description="最低工作转速")
    max_rpm: Optional[int] = Field(None, description="最高工作转速")
    status: int = Field(..., description="状态")


class BladeInfo(BaseModel):
    blade_id: str = Field(..., description="叶片ID")
    unit_id: str = Field(..., description="机组ID")
    blade_number: int = Field(..., description="叶片编号")
    stage: int = Field(..., description="级号")
    blade_type: str = Field(..., description="叶片类型")
    material: str = Field(..., description="材料")
    length_mm: float = Field(..., description="叶片长度(mm)")
    strain_gauge_count: int = Field(..., description="应变片数量")
    natural_frequency_hz: Optional[float] = Field(None, description="固有频率(Hz)")
    damping_ratio: Optional[float] = Field(None, description="阻尼比")


class WaveformShardUploadRequest(BaseModel):
    unit_id: str = Field(..., description="机组ID")
    blade_id: str = Field(..., description="叶片ID")
    channel_id: int = Field(..., description="通道ID")
    shard_id: str = Field(..., description="分片ID")
    upload_id: str = Field(..., description="上传任务ID")
    shard_index: int = Field(..., ge=0, description="分片序号")
    total_shards: int = Field(..., ge=1, description="总分片数")
    sample_rate: int = Field(..., gt=0, description="采样率(Hz)")
    rpm: float = Field(..., gt=0, description="转速(RPM)")
    start_time: datetime = Field(..., description="数据开始时间")
    sample_count: int = Field(..., gt=0, description="采样点数")
    compression: Optional[str] = Field(None, description="压缩方式: gzip/lz4")
    task_id: Optional[str] = Field(None, description="采集任务ID")


class WaveformShardUploadResponse(BaseModel):
    shard_id: str = Field(..., description="分片ID")
    upload_id: str = Field(..., description="上传任务ID")
    shard_index: int = Field(..., description="分片序号")
    success: bool = Field(..., description="是否成功")
    records_inserted: int = Field(..., description="插入记录数")
    message: Optional[str] = Field(None, description="消息")


class WaveformUploadCompleteRequest(BaseModel):
    upload_id: str = Field(..., description="上传任务ID")
    unit_id: str = Field(..., description="机组ID")
    blade_id: str = Field(..., description="叶片ID")
    total_shards: int = Field(..., description="总分片数")
    total_samples: int = Field(..., description="总采样点数")
    trigger_analysis: bool = Field(True, description="是否触发分析")


class WaveformUploadCompleteResponse(BaseModel):
    upload_id: str = Field(..., description="上传任务ID")
    success: bool = Field(..., description="是否成功")
    total_shards_received: int = Field(..., description="已接收分片数")
    analysis_triggered: bool = Field(..., description="是否已触发分析")
    analysis_job_id: Optional[str] = Field(None, description="分析任务ID")


class OrderResamplingResult(BaseModel):
    base_order: float = Field(..., description="基频阶次")
    order_values: List[float] = Field(..., description="阶次轴数据")
    amplitude_values: List[float] = Field(..., description="幅值数据")
    phase_values: List[float] = Field(..., description="相位数据")
    rpm_range: List[float] = Field(..., description="转速范围")
    analysis_window_seconds: float = Field(..., description="分析窗口(秒)")


class OrderSpectrumResult(BaseModel):
    resonance_orders: List[float] = Field(..., description="共振阶次列表")
    resonance_amplitudes: List[float] = Field(..., description="共振阶次幅值")
    harmonic_orders: List[float] = Field(..., description="谐波阶次列表")
    harmonic_amplitudes: List[float] = Field(..., description="谐波幅值")
    sideband_orders: List[float] = Field(..., description="边频阶次列表")
    sideband_amplitudes: List[float] = Field(..., description="边频幅值")
    noise_floor: float = Field(..., description="噪声基底")
    snr: float = Field(..., description="信噪比")


class FatigueDamageResult(BaseModel):
    damage_value: float = Field(..., description="损伤值")
    remaining_life_hours: float = Field(..., description="剩余寿命(小时)")
    cycle_count: int = Field(..., description="循环次数")
    max_stress: float = Field(..., description="最大应力(MPa)")
    min_stress: float = Field(..., description="最小应力(MPa)")
    mean_stress: float = Field(..., description="平均应力(MPa)")
    stress_amplitude: float = Field(..., description="应力幅值(MPa)")
    damage_accumulated: float = Field(..., description="累积损伤")


class WaveformQueryRequest(BaseModel):
    unit_id: str = Field(..., description="机组ID")
    blade_id: Optional[str] = Field(None, description="叶片ID，不填则查询所有叶片")
    channel_id: Optional[int] = Field(None, description="通道ID")
    start_time: datetime = Field(..., description="开始时间")
    end_time: datetime = Field(..., description="结束时间")
    rpm_min: Optional[float] = Field(None, description="最小转速")
    rpm_max: Optional[float] = Field(None, description="最大转速")
    limit: int = Field(1000, ge=1, le=10000, description="返回记录数限制")

    @field_validator("end_time")
    def end_time_must_be_after_start(cls, v, values):
        if "start_time" in values.data and v <= values.data["start_time"]:
            raise ValueError("结束时间必须晚于开始时间")
        return v


class WaveformQueryResponse(BaseModel):
    time: datetime = Field(..., description="时间戳")
    unit_id: str = Field(..., description="机组ID")
    blade_id: str = Field(..., description="叶片ID")
    channel_id: int = Field(..., description="通道ID")
    sample_rate: int = Field(..., description="采样率")
    rpm: float = Field(..., description="转速")
    strain_values: Optional[List[float]] = Field(None, description="应变波形数据")
    sample_count: int = Field(..., description="采样点数")


class OrderSpectrumQueryRequest(BaseModel):
    unit_id: str = Field(..., description="机组ID")
    blade_id: Optional[str] = Field(None, description="叶片ID")
    start_time: datetime = Field(..., description="开始时间")
    end_time: datetime = Field(..., description="结束时间")
    rpm_min: Optional[float] = Field(None, description="最小转速")
    rpm_max: Optional[float] = Field(None, description="最大转速")
    limit: int = Field(1000, ge=1, le=10000, description="返回记录数限制")


class FatigueDamageQueryRequest(BaseModel):
    unit_id: str = Field(..., description="机组ID")
    blade_id: Optional[str] = Field(None, description="叶片ID")
    start_time: datetime = Field(..., description="开始时间")
    end_time: datetime = Field(..., description="结束时间")
    min_damage: Optional[float] = Field(None, description="最小损伤值过滤")
    aggregation: Optional[str] = Field(None, description="聚合方式: hourly/daily/weekly")


class WaveformAnalysisRequest(BaseModel):
    unit_id: str = Field(..., description="机组ID")
    blade_id: str = Field(..., description="叶片ID")
    channel_id: int = Field(..., description="通道ID")
    start_time: datetime = Field(..., description="开始时间")
    end_time: datetime = Field(..., description="结束时间")
    blade_count: int = Field(..., gt=0, description="叶片数")
    rpm: Optional[float] = Field(None, description="指定转速，不填则使用数据中的转速")
    options: Optional[Dict[str, Any]] = Field(None, description="分析选项")


class AnalysisFailureRecord(BaseModel):
    failure_id: int = Field(..., description="失败记录ID")
    time: datetime = Field(..., description="失败时间")
    unit_id: str = Field(..., description="机组ID")
    blade_id: str = Field(..., description="叶片ID")
    upload_id: str = Field(..., description="上传ID")
    error_code: int = Field(..., description="错误码")
    error_message: str = Field(..., description="错误消息")
    retry_count: int = Field(..., description="重试次数")
    resolved: bool = Field(..., description="是否已解决")
    created_at: datetime = Field(..., description="创建时间")


class StatsResponse(BaseModel):
    total_units: int = Field(..., description="机组总数")
    total_blades: int = Field(..., description="叶片总数")
    total_channels: int = Field(..., description="通道总数")
    total_waveform_records: int = Field(..., description="波形记录总数")
    total_analysis_records: int = Field(..., description="分析记录总数")
    total_damage_records: int = Field(..., description="损伤记录总数")
