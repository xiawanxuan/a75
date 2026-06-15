from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime,
    SmallInteger, BigInteger, DECIMAL, Date, Text, JSON,
    Index, ForeignKey, UniqueConstraint
)
from sqlalchemy.dialects.mysql import JSON as MYSQL_JSON
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class PowerPlant(Base):
    __tablename__ = "power_plants"

    plant_id = Column(String(32), primary_key=True, comment="电厂ID")
    plant_name = Column(String(128), nullable=False, comment="电厂名称")
    location = Column(String(256), comment="地理位置")
    capacity_mw = Column(DECIMAL(10, 2), comment="装机容量(MW)")
    commission_date = Column(Date, comment="投运日期")
    operator = Column(String(64), comment="运营方")
    contact_phone = Column(String(32), comment="联系电话")
    contact_email = Column(String(128), comment="联系邮箱")
    status = Column(SmallInteger, default=1, comment="状态: 0-停用 1-正常")
    created_at = Column(DateTime, comment="创建时间")
    updated_at = Column(DateTime, comment="更新时间")

    units = relationship("Unit", back_populates="plant")

    __table_args__ = (
        Index("idx_plant_name", "plant_name"),
        {"comment": "电厂信息表"}
    )


class Unit(Base):
    __tablename__ = "units"

    unit_id = Column(String(32), primary_key=True, comment="机组ID")
    plant_id = Column(String(32), ForeignKey("power_plants.plant_id"), nullable=False, comment="所属电厂ID")
    unit_name = Column(String(128), nullable=False, comment="机组名称")
    unit_type = Column(String(32), nullable=False, comment="机组类型")
    capacity_mw = Column(DECIMAL(10, 2), nullable=False, comment="机组容量(MW)")
    manufacturer = Column(String(64), comment="制造商")
    model = Column(String(64), comment="型号")
    rated_rpm = Column(Integer, nullable=False, comment="额定转速(RPM)")
    min_rpm = Column(Integer, comment="最低工作转速")
    max_rpm = Column(Integer, comment="最高工作转速")
    commission_date = Column(Date, comment="投运日期")
    total_operation_hours = Column(BigInteger, default=0, comment="累计运行小时")
    total_startups = Column(Integer, default=0, comment="累计启动次数")
    last_maintenance_date = Column(Date, comment="上次检修日期")
    next_maintenance_date = Column(Date, comment="下次检修日期")
    status = Column(SmallInteger, default=1, comment="状态: 0-停运 1-运行 2-检修")
    created_at = Column(DateTime, comment="创建时间")
    updated_at = Column(DateTime, comment="更新时间")

    plant = relationship("PowerPlant", back_populates="units")
    blades = relationship("Blade", back_populates="unit")

    __table_args__ = (
        Index("idx_plant_id", "plant_id"),
        Index("idx_unit_status", "status"),
        {"comment": "机组台账表"}
    )


class Blade(Base):
    __tablename__ = "blades"

    blade_id = Column(String(32), primary_key=True, comment="叶片ID")
    unit_id = Column(String(32), ForeignKey("units.unit_id"), nullable=False, comment="所属机组ID")
    blade_number = Column(Integer, nullable=False, comment="叶片编号")
    stage = Column(Integer, nullable=False, comment="级号")
    blade_type = Column(String(32), nullable=False, comment="叶片类型")
    material = Column(String(64), nullable=False, comment="材料")
    material_spec = Column(String(128), comment="材料规格")
    length_mm = Column(DECIMAL(10, 3), nullable=False, comment="叶片长度(mm)")
    root_width_mm = Column(DECIMAL(10, 3), comment="叶根宽度(mm)")
    tip_width_mm = Column(DECIMAL(10, 3), comment="叶顶宽度(mm)")
    thickness_mm = Column(DECIMAL(10, 3), comment="最大厚度(mm)")
    design_life_hours = Column(BigInteger, comment="设计寿命(小时)")
    operating_life_hours = Column(BigInteger, default=0, comment="已运行小时")
    strain_gauge_count = Column(Integer, nullable=False, comment="应变片数量")
    natural_frequency_hz = Column(DECIMAL(10, 3), comment="固有频率(Hz)")
    damping_ratio = Column(DECIMAL(6, 4), comment="阻尼比")
    installation_date = Column(Date, comment="安装日期")
    last_inspection_date = Column(Date, comment="上次检测日期")
    status = Column(SmallInteger, default=1, comment="状态: 0-报废 1-正常 2-预警")
    created_at = Column(DateTime, comment="创建时间")
    updated_at = Column(DateTime, comment="更新时间")

    unit = relationship("Unit", back_populates="blades")
    channels = relationship("MeasurementChannel", back_populates="blade")

    __table_args__ = (
        UniqueConstraint("unit_id", "stage", "blade_number", name="uk_unit_blade_number"),
        Index("idx_unit_id", "unit_id"),
        {"comment": "叶片几何参数表"}
    )


class MeasurementChannel(Base):
    __tablename__ = "measurement_channels"

    channel_id = Column(SmallInteger, primary_key=True, autoincrement=True, comment="通道ID")
    unit_id = Column(String(32), ForeignKey("units.unit_id"), nullable=False, comment="所属机组ID")
    blade_id = Column(String(32), ForeignKey("blades.blade_id"), nullable=False, comment="所属叶片ID")
    channel_number = Column(Integer, nullable=False, comment="通道编号")
    sensor_type = Column(String(32), nullable=False, comment="传感器类型")
    sensor_model = Column(String(64), comment="传感器型号")
    location_mm = Column(DECIMAL(10, 3), comment="安装位置距叶根(mm)")
    angle_deg = Column(DECIMAL(5, 2), comment="安装角度(度)")
    sensitivity_ue = Column(DECIMAL(10, 4), comment="灵敏度(με/V)")
    bridge_voltage = Column(DECIMAL(6, 2), comment="桥压(V)")
    sample_rate_hz = Column(Integer, nullable=False, default=25600, comment="采样率(Hz)")
    filter_low_hz = Column(DECIMAL(8, 2), comment="低通滤波(Hz)")
    filter_high_hz = Column(DECIMAL(8, 2), comment="高通滤波(Hz)")
    gain = Column(DECIMAL(8, 2), default=1.0, comment="增益")
    calibration_date = Column(Date, comment="校准日期")
    calibration_due_date = Column(Date, comment="校准到期日期")
    status = Column(SmallInteger, default=1, comment="状态: 0-故障 1-正常")
    created_at = Column(DateTime, comment="创建时间")
    updated_at = Column(DateTime, comment="更新时间")

    blade = relationship("Blade", back_populates="channels")

    __table_args__ = (
        UniqueConstraint("unit_id", "blade_id", "channel_number", name="uk_unit_channel"),
        Index("idx_unit_id", "unit_id"),
        Index("idx_blade_id", "blade_id"),
        {"comment": "测量通道配置表"}
    )


class MaterialSNCurve(Base):
    __tablename__ = "material_sn_curves"

    curve_id = Column(Integer, primary_key=True, autoincrement=True, comment="曲线ID")
    material = Column(String(64), nullable=False, comment="材料名称")
    temperature_c = Column(Integer, nullable=False, comment="温度(°C)")
    sn_slope = Column(DECIMAL(10, 4), nullable=False, comment="S-N曲线斜率")
    sn_intercept = Column(DECIMAL(15, 4), nullable=False, comment="S-N曲线截距")
    fatigue_limit_mpa = Column(DECIMAL(10, 4), comment="疲劳极限(MPa)")
    ultimate_strength_mpa = Column(DECIMAL(10, 4), comment="极限强度(MPa)")
    yield_strength_mpa = Column(DECIMAL(10, 4), comment="屈服强度(MPa)")
    elastic_modulus_gpa = Column(DECIMAL(8, 2), comment="弹性模量(GPa)")
    poisson_ratio = Column(DECIMAL(6, 4), default=0.3, comment="泊松比")
    reference = Column(String(256), comment="参考标准")
    created_at = Column(DateTime, comment="创建时间")

    __table_args__ = (
        UniqueConstraint("material", "temperature_c", name="uk_material_temp"),
        {"comment": "材料S-N曲线参数表"}
    )


class DataCollectionTask(Base):
    __tablename__ = "data_collection_tasks"

    task_id = Column(String(64), primary_key=True, comment="任务ID")
    unit_id = Column(String(32), ForeignKey("units.unit_id"), nullable=False, comment="机组ID")
    task_type = Column(String(32), nullable=False, comment="任务类型")
    planned_start = Column(DateTime, nullable=False, comment="计划开始时间")
    planned_end = Column(DateTime, nullable=False, comment="计划结束时间")
    actual_start = Column(DateTime, comment="实际开始时间")
    actual_end = Column(DateTime, comment="实际结束时间")
    sample_rate_hz = Column(Integer, comment="采样率(Hz)")
    blade_ids = Column(MYSQL_JSON, comment="采集叶片列表")
    channel_ids = Column(MYSQL_JSON, comment="采集通道列表")
    trigger_condition = Column(String(256), comment="触发条件")
    status = Column(SmallInteger, default=0, comment="状态: 0-待执行 1-执行中 2-已完成 3-失败")
    upload_count = Column(Integer, default=0, comment="上传分片数")
    total_size_mb = Column(DECIMAL(15, 3), default=0, comment="总数据量(MB)")
    operator = Column(String(64), comment="操作人员")
    remark = Column(Text, comment="备注")
    created_at = Column(DateTime, comment="创建时间")
    updated_at = Column(DateTime, comment="更新时间")

    __table_args__ = (
        Index("idx_unit_id", "unit_id"),
        Index("idx_task_status", "status"),
        {"comment": "数据采集任务表"}
    )


class AlertRule(Base):
    __tablename__ = "alert_rules"

    rule_id = Column(Integer, primary_key=True, autoincrement=True, comment="规则ID")
    rule_name = Column(String(128), nullable=False, comment="规则名称")
    unit_id = Column(String(32), ForeignKey("units.unit_id"), comment="关联机组ID")
    metric_type = Column(String(32), nullable=False, comment="指标类型")
    warning_threshold = Column(DECIMAL(15, 4), nullable=False, comment="预警阈值")
    alarm_threshold = Column(DECIMAL(15, 4), nullable=False, comment="报警阈值")
    evaluation_window = Column(Integer, default=1, comment="评估窗口(分钟)")
    consecutive_count = Column(Integer, default=1, comment="连续超限次数")
    severity = Column(SmallInteger, nullable=False, comment="严重程度: 1-低 2-中 3-高")
    enabled = Column(SmallInteger, default=1, comment="是否启用")
    notification_type = Column(String(32), default="email", comment="通知方式")
    notification_target = Column(String(256), comment="通知目标")
    created_at = Column(DateTime, comment="创建时间")
    updated_at = Column(DateTime, comment="更新时间")

    __table_args__ = (
        Index("idx_unit_id", "unit_id"),
        {"comment": "告警规则表"}
    )


class User(Base):
    __tablename__ = "users"

    user_id = Column(BigInteger, primary_key=True, autoincrement=True, comment="用户ID")
    username = Column(String(64), nullable=False, comment="用户名")
    password_hash = Column(String(256), nullable=False, comment="密码哈希")
    real_name = Column(String(64), comment="真实姓名")
    email = Column(String(128), comment="邮箱")
    phone = Column(String(32), comment="手机号")
    role = Column(String(32), nullable=False, default="viewer", comment="角色")
    plant_ids = Column(MYSQL_JSON, comment="可访问电厂列表")
    last_login = Column(DateTime, comment="最后登录时间")
    status = Column(SmallInteger, default=1, comment="状态: 0-禁用 1-正常")
    created_at = Column(DateTime, comment="创建时间")
    updated_at = Column(DateTime, comment="更新时间")

    __table_args__ = (
        UniqueConstraint("username", name="uk_username"),
        {"comment": "用户表"}
    )
