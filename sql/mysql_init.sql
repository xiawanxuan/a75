-- =======================================================
-- 燃气电厂机组健康诊断平台 - MySQL 初始化脚本
-- 功能：存储机组台账、叶片几何参数、用户权限等元数据
-- =======================================================

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- =======================================================
-- 1. 电厂信息表
-- =======================================================
DROP TABLE IF EXISTS `power_plants`;
CREATE TABLE `power_plants` (
    `plant_id`          VARCHAR(32)       NOT NULL COMMENT '电厂ID',
    `plant_name`        VARCHAR(128)      NOT NULL COMMENT '电厂名称',
    `location`          VARCHAR(256)      COMMENT '地理位置',
    `capacity_mw`       DECIMAL(10,2)     COMMENT '装机容量(MW)',
    `commission_date`   DATE              COMMENT '投运日期',
    `operator`          VARCHAR(64)       COMMENT '运营方',
    `contact_phone`     VARCHAR(32)       COMMENT '联系电话',
    `contact_email`     VARCHAR(128)      COMMENT '联系邮箱',
    `status`            TINYINT           DEFAULT 1 COMMENT '状态: 0-停用 1-正常',
    `created_at`        DATETIME          DEFAULT CURRENT_TIMESTAMP,
    `updated_at`        DATETIME          DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`plant_id`),
    KEY `idx_plant_name` (`plant_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='电厂信息表';

-- =======================================================
-- 2. 机组台账表
-- =======================================================
DROP TABLE IF EXISTS `units`;
CREATE TABLE `units` (
    `unit_id`           VARCHAR(32)       NOT NULL COMMENT '机组ID',
    `plant_id`          VARCHAR(32)       NOT NULL COMMENT '所属电厂ID',
    `unit_name`         VARCHAR(128)      NOT NULL COMMENT '机组名称',
    `unit_type`         VARCHAR(32)       NOT NULL COMMENT '机组类型: 燃气轮机/蒸汽轮机',
    `capacity_mw`       DECIMAL(10,2)     NOT NULL COMMENT '机组容量(MW)',
    `manufacturer`      VARCHAR(64)       COMMENT '制造商',
    `model`             VARCHAR(64)       COMMENT '型号',
    `rated_rpm`         INTEGER           NOT NULL COMMENT '额定转速(RPM)',
    `min_rpm`           INTEGER           COMMENT '最低工作转速',
    `max_rpm`           INTEGER           COMMENT '最高工作转速',
    `commission_date`   DATE              COMMENT '投运日期',
    `total_operation_hours` BIGINT        DEFAULT 0 COMMENT '累计运行小时',
    `total_startups`    INTEGER           DEFAULT 0 COMMENT '累计启动次数',
    `last_maintenance_date` DATE          COMMENT '上次检修日期',
    `next_maintenance_date` DATE          COMMENT '下次检修日期',
    `status`            TINYINT           DEFAULT 1 COMMENT '状态: 0-停运 1-运行 2-检修',
    `created_at`        DATETIME          DEFAULT CURRENT_TIMESTAMP,
    `updated_at`        DATETIME          DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`unit_id`),
    KEY `idx_plant_id` (`plant_id`),
    KEY `idx_unit_status` (`status`),
    CONSTRAINT `fk_units_plant` FOREIGN KEY (`plant_id`) REFERENCES `power_plants` (`plant_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='机组台账表';

-- =======================================================
-- 3. 叶片几何参数表
-- =======================================================
DROP TABLE IF EXISTS `blades`;
CREATE TABLE `blades` (
    `blade_id`          VARCHAR(32)       NOT NULL COMMENT '叶片ID',
    `unit_id`           VARCHAR(32)       NOT NULL COMMENT '所属机组ID',
    `blade_number`      INTEGER           NOT NULL COMMENT '叶片编号',
    `stage`             INTEGER           NOT NULL COMMENT '级号',
    `blade_type`        VARCHAR(32)       NOT NULL COMMENT '叶片类型: 静叶/动叶',
    `material`          VARCHAR(64)       NOT NULL COMMENT '材料',
    `material_spec`     VARCHAR(128)      COMMENT '材料规格',
    `length_mm`         DECIMAL(10,3)     NOT NULL COMMENT '叶片长度(mm)',
    `root_width_mm`     DECIMAL(10,3)     COMMENT '叶根宽度(mm)',
    `tip_width_mm`      DECIMAL(10,3)     COMMENT '叶顶宽度(mm)',
    `thickness_mm`      DECIMAL(10,3)     COMMENT '最大厚度(mm)',
    `design_life_hours` BIGINT            COMMENT '设计寿命(小时)',
    `operating_life_hours` BIGINT         DEFAULT 0 COMMENT '已运行小时',
    `strain_gauge_count` INTEGER          NOT NULL COMMENT '应变片数量',
    `natural_frequency_hz` DECIMAL(10,3)  COMMENT '固有频率(Hz)',
    `damping_ratio`     DECIMAL(6,4)      COMMENT '阻尼比',
    `installation_date` DATE              COMMENT '安装日期',
    `last_inspection_date` DATE           COMMENT '上次检测日期',
    `status`            TINYINT           DEFAULT 1 COMMENT '状态: 0-报废 1-正常 2-预警',
    `created_at`        DATETIME          DEFAULT CURRENT_TIMESTAMP,
    `updated_at`        DATETIME          DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`blade_id`),
    UNIQUE KEY `uk_unit_blade_number` (`unit_id`, `stage`, `blade_number`),
    KEY `idx_unit_id` (`unit_id`),
    CONSTRAINT `fk_blades_unit` FOREIGN KEY (`unit_id`) REFERENCES `units` (`unit_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='叶片几何参数表';

-- =======================================================
-- 4. 测量通道配置表
-- =======================================================
DROP TABLE IF EXISTS `measurement_channels`;
CREATE TABLE `measurement_channels` (
    `channel_id`        SMALLINT          NOT NULL AUTO_INCREMENT COMMENT '通道ID',
    `unit_id`           VARCHAR(32)       NOT NULL COMMENT '所属机组ID',
    `blade_id`          VARCHAR(32)       NOT NULL COMMENT '所属叶片ID',
    `channel_number`    INTEGER           NOT NULL COMMENT '通道编号',
    `sensor_type`       VARCHAR(32)       NOT NULL COMMENT '传感器类型: 应变片/加速度计',
    `sensor_model`      VARCHAR(64)       COMMENT '传感器型号',
    `location_mm`       DECIMAL(10,3)     COMMENT '安装位置距叶根(mm)',
    `angle_deg`         DECIMAL(5,2)      COMMENT '安装角度(度)',
    `sensitivity_ue`    DECIMAL(10,4)     COMMENT '灵敏度(με/V)',
    `bridge_voltage`    DECIMAL(6,2)      COMMENT '桥压(V)',
    `sample_rate_hz`    INTEGER           NOT NULL DEFAULT 25600 COMMENT '采样率(Hz)',
    `filter_low_hz`     DECIMAL(8,2)      COMMENT '低通滤波(Hz)',
    `filter_high_hz`    DECIMAL(8,2)      COMMENT '高通滤波(Hz)',
    `gain`              DECIMAL(8,2)      DEFAULT 1.0 COMMENT '增益',
    `calibration_date`  DATE              COMMENT '校准日期',
    `calibration_due_date` DATE          COMMENT '校准到期日期',
    `status`            TINYINT           DEFAULT 1 COMMENT '状态: 0-故障 1-正常',
    `created_at`        DATETIME          DEFAULT CURRENT_TIMESTAMP,
    `updated_at`        DATETIME          DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`channel_id`),
    UNIQUE KEY `uk_unit_channel` (`unit_id`, `blade_id`, `channel_number`),
    KEY `idx_unit_id` (`unit_id`),
    KEY `idx_blade_id` (`blade_id`),
    CONSTRAINT `fk_channels_unit` FOREIGN KEY (`unit_id`) REFERENCES `units` (`unit_id`),
    CONSTRAINT `fk_channels_blade` FOREIGN KEY (`blade_id`) REFERENCES `blades` (`blade_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='测量通道配置表';

-- =======================================================
-- 5. 材料S-N曲线参数表
-- =======================================================
DROP TABLE IF EXISTS `material_sn_curves`;
CREATE TABLE `material_sn_curves` (
    `curve_id`          INTEGER           NOT NULL AUTO_INCREMENT COMMENT '曲线ID',
    `material`          VARCHAR(64)       NOT NULL COMMENT '材料名称',
    `temperature_c`     INTEGER           NOT NULL COMMENT '温度(°C)',
    `sn_slope`          DECIMAL(10,4)     NOT NULL COMMENT 'S-N曲线斜率',
    `sn_intercept`      DECIMAL(15,4)     NOT NULL COMMENT 'S-N曲线截距',
    `fatigue_limit_mpa` DECIMAL(10,4)     COMMENT '疲劳极限(MPa)',
    `ultimate_strength_mpa` DECIMAL(10,4) COMMENT '极限强度(MPa)',
    `yield_strength_mpa` DECIMAL(10,4)    COMMENT '屈服强度(MPa)',
    `elastic_modulus_gpa` DECIMAL(8,2)    COMMENT '弹性模量(GPa)',
    `poisson_ratio`     DECIMAL(6,4)      DEFAULT 0.3 COMMENT '泊松比',
    `reference`         VARCHAR(256)      COMMENT '参考标准',
    `created_at`        DATETIME          DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`curve_id`),
    UNIQUE KEY `uk_material_temp` (`material`, `temperature_c`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='材料S-N曲线参数表';

-- =======================================================
-- 6. 数据采集任务表
-- =======================================================
DROP TABLE IF EXISTS `data_collection_tasks`;
CREATE TABLE `data_collection_tasks` (
    `task_id`           VARCHAR(64)       NOT NULL COMMENT '任务ID',
    `unit_id`           VARCHAR(32)       NOT NULL COMMENT '机组ID',
    `task_type`         VARCHAR(32)       NOT NULL COMMENT '任务类型: 日常巡检/启动过程/停机过程/特定工况',
    `planned_start`     DATETIME          NOT NULL COMMENT '计划开始时间',
    `planned_end`       DATETIME          NOT NULL COMMENT '计划结束时间',
    `actual_start`      DATETIME          COMMENT '实际开始时间',
    `actual_end`        DATETIME          COMMENT '实际结束时间',
    `sample_rate_hz`    INTEGER           COMMENT '采样率(Hz)',
    `blade_ids`         JSON              COMMENT '采集叶片列表',
    `channel_ids`       JSON              COMMENT '采集通道列表',
    `trigger_condition` VARCHAR(256)      COMMENT '触发条件',
    `status`            TINYINT           DEFAULT 0 COMMENT '状态: 0-待执行 1-执行中 2-已完成 3-失败',
    `upload_count`      INTEGER           DEFAULT 0 COMMENT '上传分片数',
    `total_size_mb`     DECIMAL(15,3)     DEFAULT 0 COMMENT '总数据量(MB)',
    `operator`          VARCHAR(64)       COMMENT '操作人员',
    `remark`            TEXT              COMMENT '备注',
    `created_at`        DATETIME          DEFAULT CURRENT_TIMESTAMP,
    `updated_at`        DATETIME          DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`task_id`),
    KEY `idx_unit_id` (`unit_id`),
    KEY `idx_task_status` (`status`),
    CONSTRAINT `fk_tasks_unit` FOREIGN KEY (`unit_id`) REFERENCES `units` (`unit_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='数据采集任务表';

-- =======================================================
-- 7. 告警规则表
-- =======================================================
DROP TABLE IF EXISTS `alert_rules`;
CREATE TABLE `alert_rules` (
    `rule_id`           INTEGER           NOT NULL AUTO_INCREMENT COMMENT '规则ID',
    `rule_name`         VARCHAR(128)      NOT NULL COMMENT '规则名称',
    `unit_id`           VARCHAR(32)       COMMENT '关联机组ID (空表示全局)',
    `metric_type`       VARCHAR(32)       NOT NULL COMMENT '指标类型: 损伤/应力/阶次幅值/SNR',
    `warning_threshold` DECIMAL(15,4)     NOT NULL COMMENT '预警阈值',
    `alarm_threshold`   DECIMAL(15,4)     NOT NULL COMMENT '报警阈值',
    `evaluation_window` INTEGER           DEFAULT 1 COMMENT '评估窗口(分钟)',
    `consecutive_count` INTEGER           DEFAULT 1 COMMENT '连续超限次数',
    `severity`          TINYINT           NOT NULL COMMENT '严重程度: 1-低 2-中 3-高',
    `enabled`           TINYINT           DEFAULT 1 COMMENT '是否启用',
    `notification_type` VARCHAR(32)       DEFAULT 'email' COMMENT '通知方式',
    `notification_target` VARCHAR(256)    COMMENT '通知目标',
    `created_at`        DATETIME          DEFAULT CURRENT_TIMESTAMP,
    `updated_at`        DATETIME          DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`rule_id`),
    KEY `idx_unit_id` (`unit_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='告警规则表';

-- =======================================================
-- 8. 用户表
-- =======================================================
DROP TABLE IF EXISTS `users`;
CREATE TABLE `users` (
    `user_id`           BIGINT            NOT NULL AUTO_INCREMENT COMMENT '用户ID',
    `username`          VARCHAR(64)       NOT NULL COMMENT '用户名',
    `password_hash`     VARCHAR(256)      NOT NULL COMMENT '密码哈希',
    `real_name`         VARCHAR(64)       COMMENT '真实姓名',
    `email`             VARCHAR(128)      COMMENT '邮箱',
    `phone`             VARCHAR(32)       COMMENT '手机号',
    `role`              VARCHAR(32)       NOT NULL DEFAULT 'viewer' COMMENT '角色: admin/engineer/viewer',
    `plant_ids`         JSON              COMMENT '可访问电厂列表',
    `last_login`        DATETIME          COMMENT '最后登录时间',
    `status`            TINYINT           DEFAULT 1 COMMENT '状态: 0-禁用 1-正常',
    `created_at`        DATETIME          DEFAULT CURRENT_TIMESTAMP,
    `updated_at`        DATETIME          DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`user_id`),
    UNIQUE KEY `uk_username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户表';

-- =======================================================
-- 9. 插入初始化数据
-- =======================================================

INSERT INTO `power_plants` (`plant_id`, `plant_name`, `location`, `capacity_mw`, `operator`) VALUES
('PLANT001', '华能北京热电厂', '北京市朝阳区', 1200.00, '华能集团'),
('PLANT002', '大唐南京发电厂', '江苏省南京市', 800.00, '大唐集团');

INSERT INTO `units` (`unit_id`, `plant_id`, `unit_name`, `unit_type`, `capacity_mw`, `manufacturer`, `model`, `rated_rpm`, `min_rpm`, `max_rpm`, `commission_date`) VALUES
('UNIT001', 'PLANT001', '1号燃气轮机机组', '燃气轮机', 400.00, 'GE', '9HA.01', 3000, 600, 3600, '2020-06-15'),
('UNIT002', 'PLANT001', '2号燃气轮机机组', '燃气轮机', 400.00, 'GE', '9HA.01', 3000, 600, 3600, '2020-12-20'),
('UNIT003', 'PLANT002', '1号联合循环机组', '燃气轮机', 400.00, '西门子', 'SGT5-8000H', 3000, 600, 3600, '2019-03-10');

INSERT INTO `blades` (`blade_id`, `unit_id`, `blade_number`, `stage`, `blade_type`, `material`, `length_mm`, `thickness_mm`, `design_life_hours`, `strain_gauge_count`, `natural_frequency_hz`, `damping_ratio`, `installation_date`) VALUES
('BLADE001', 'UNIT001', 1, 1, '动叶', 'GTD-111', 180.500, 15.200, 100000, 2, 128.500, 0.0050, '2020-06-10'),
('BLADE002', 'UNIT001', 2, 1, '动叶', 'GTD-111', 180.500, 15.200, 100000, 2, 128.500, 0.0050, '2020-06-10'),
('BLADE003', 'UNIT001', 3, 1, '动叶', 'GTD-111', 180.500, 15.200, 100000, 2, 128.500, 0.0050, '2020-06-10'),
('BLADE004', 'UNIT002', 1, 1, '动叶', 'GTD-111', 180.500, 15.200, 100000, 2, 128.500, 0.0050, '2020-12-15'),
('BLADE005', 'UNIT003', 1, 1, '动叶', 'INCONEL 738', 200.000, 16.500, 100000, 2, 135.200, 0.0045, '2019-03-05');

INSERT INTO `measurement_channels` (`unit_id`, `blade_id`, `channel_number`, `sensor_type`, `location_mm`, `angle_deg`, `sensitivity_ue`, `bridge_voltage`, `sample_rate_hz`, `filter_low_hz`, `filter_high_hz`, `gain`) VALUES
('UNIT001', 'BLADE001', 1, '应变片', 90.000, 0.00, 2.0500, 5.00, 25600, 0.50, 10000.00, 1000.00),
('UNIT001', 'BLADE001', 2, '应变片', 90.000, 90.00, 2.0450, 5.00, 25600, 0.50, 10000.00, 1000.00),
('UNIT001', 'BLADE002', 1, '应变片', 90.000, 0.00, 2.0520, 5.00, 25600, 0.50, 10000.00, 1000.00),
('UNIT001', 'BLADE002', 2, '应变片', 90.000, 90.00, 2.0480, 5.00, 25600, 0.50, 10000.00, 1000.00),
('UNIT001', 'BLADE003', 1, '应变片', 90.000, 0.00, 2.0490, 5.00, 25600, 0.50, 10000.00, 1000.00),
('UNIT002', 'BLADE004', 1, '应变片', 90.000, 0.00, 2.0510, 5.00, 25600, 0.50, 10000.00, 1000.00),
('UNIT003', 'BLADE005', 1, '应变片', 100.000, 0.00, 2.0600, 5.00, 25600, 0.50, 10000.00, 1000.00);

INSERT INTO `material_sn_curves` (`material`, `temperature_c`, `sn_slope`, `sn_intercept`, `fatigue_limit_mpa`, `ultimate_strength_mpa`, `yield_strength_mpa`, `elastic_modulus_gpa`, `reference`) VALUES
('GTD-111', 600, 5.0000, 1500.0000, 50.0000, 800.0000, 650.0000, 185.00, 'GE Material Spec'),
('GTD-111', 700, 5.2000, 1450.0000, 45.0000, 750.0000, 600.0000, 180.00, 'GE Material Spec'),
('INCONEL 738', 650, 4.8000, 1600.0000, 55.0000, 850.0000, 700.0000, 190.00, 'Special Metals Spec'),
('INCONEL 738', 750, 5.0000, 1550.0000, 50.0000, 800.0000, 650.0000, 185.00, 'Special Metals Spec');

INSERT INTO `alert_rules` (`rule_name`, `unit_id`, `metric_type`, `warning_threshold`, `alarm_threshold`, `evaluation_window`, `consecutive_count`, `severity`) VALUES
('叶片疲劳损伤预警', NULL, 'damage', 0.0001, 0.001, 5, 3, 2),
('叶片应力超限预警', NULL, 'stress', 600.0, 700.0, 1, 1, 3),
('阶次幅值异常', NULL, 'order_amplitude', 500.0, 1000.0, 2, 2, 2),
('信噪比过低', NULL, 'snr', 3.0, 1.5, 1, 1, 1);

INSERT INTO `users` (`username`, `password_hash`, `real_name`, `email`, `role`, `plant_ids`) VALUES
('admin', '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewYGyJqHkBOj6U7u', '系统管理员', 'admin@turbine.com', 'admin', '["PLANT001","PLANT002"]'),
('engineer01', '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewYGyJqHkBOj6U7u', '张工', 'engineer01@turbine.com', 'engineer', '["PLANT001"]'),
('viewer01', '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewYGyJqHkBOj6U7u', '李工', 'viewer01@turbine.com', 'viewer', '["PLANT001","PLANT002"]');

SET FOREIGN_KEY_CHECKS = 1;
