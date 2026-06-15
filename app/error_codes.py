from enum import IntEnum
from typing import Dict, Any


class ErrorCode(IntEnum):
    SUCCESS = 0

    PARAM_VALIDATION_ERROR = 10001
    PARAM_MISSING_ERROR = 10002
    PARAM_TYPE_ERROR = 10003

    UNIT_NOT_FOUND = 20001
    BLADE_NOT_FOUND = 20002
    CHANNEL_NOT_FOUND = 20003
    TASK_NOT_FOUND = 20004

    WAVEFORM_EMPTY = 30001
    WAVEFORM_TOO_LARGE = 30002
    WAVEFORM_FORMAT_ERROR = 30003
    WAVEFORM_PARSE_ERROR = 30004
    WAVEFORM_SAMPLE_RATE_MISMATCH = 30005

    ORDER_RESAMPLING_FAILED = 40001
    ORDER_RESAMPLING_RPM_INVALID = 40002
    ORDER_RESAMPLING_NO_DATA = 40003

    SPECTRAL_DECOMPOSITION_FAILED = 50001
    SPECTRAL_DECOMPOSITION_NO_PEAK = 50002

    FATIGUE_CALCULATION_FAILED = 60001
    FATIGUE_MATERIAL_NOT_FOUND = 60002
    FATIGUE_NO_CYCLES = 60003

    DATABASE_CONNECTION_ERROR = 70001
    DATABASE_QUERY_ERROR = 70002
    DATABASE_INSERT_ERROR = 70003
    DATABASE_BATCH_INSERT_ERROR = 70004

    RATE_LIMIT_EXCEEDED = 80001
    CONCURRENT_LIMIT_EXCEEDED = 80002

    AUTHENTICATION_FAILED = 90001
    PERMISSION_DENIED = 90002

    INTERNAL_SERVER_ERROR = 99999


ERROR_MESSAGES: Dict[int, Dict[str, Any]] = {
    ErrorCode.SUCCESS: {
        "message": "success",
        "http_code": 200,
        "retryable": False
    },

    ErrorCode.PARAM_VALIDATION_ERROR: {
        "message": "参数校验失败",
        "http_code": 400,
        "retryable": False
    },
    ErrorCode.PARAM_MISSING_ERROR: {
        "message": "缺少必要参数",
        "http_code": 400,
        "retryable": False
    },
    ErrorCode.PARAM_TYPE_ERROR: {
        "message": "参数类型错误",
        "http_code": 400,
        "retryable": False
    },

    ErrorCode.UNIT_NOT_FOUND: {
        "message": "机组不存在",
        "http_code": 404,
        "retryable": False
    },
    ErrorCode.BLADE_NOT_FOUND: {
        "message": "叶片不存在",
        "http_code": 404,
        "retryable": False
    },
    ErrorCode.CHANNEL_NOT_FOUND: {
        "message": "通道不存在",
        "http_code": 404,
        "retryable": False
    },
    ErrorCode.TASK_NOT_FOUND: {
        "message": "任务不存在",
        "http_code": 404,
        "retryable": False
    },

    ErrorCode.WAVEFORM_EMPTY: {
        "message": "波形数据为空",
        "http_code": 400,
        "retryable": False
    },
    ErrorCode.WAVEFORM_TOO_LARGE: {
        "message": "波形数据过大",
        "http_code": 413,
        "retryable": True
    },
    ErrorCode.WAVEFORM_FORMAT_ERROR: {
        "message": "波形格式错误",
        "http_code": 400,
        "retryable": False
    },
    ErrorCode.WAVEFORM_PARSE_ERROR: {
        "message": "波形解析失败",
        "http_code": 400,
        "retryable": False
    },
    ErrorCode.WAVEFORM_SAMPLE_RATE_MISMATCH: {
        "message": "采样率不匹配",
        "http_code": 400,
        "retryable": False
    },

    ErrorCode.ORDER_RESAMPLING_FAILED: {
        "message": "阶次重采样失败",
        "http_code": 500,
        "retryable": True
    },
    ErrorCode.ORDER_RESAMPLING_RPM_INVALID: {
        "message": "转速数据无效",
        "http_code": 400,
        "retryable": False
    },
    ErrorCode.ORDER_RESAMPLING_NO_DATA: {
        "message": "重采样数据不足",
        "http_code": 400,
        "retryable": False
    },

    ErrorCode.SPECTRAL_DECOMPOSITION_FAILED: {
        "message": "阶次谱分解失败",
        "http_code": 500,
        "retryable": True
    },
    ErrorCode.SPECTRAL_DECOMPOSITION_NO_PEAK: {
        "message": "未检测到有效峰值",
        "http_code": 500,
        "retryable": False
    },

    ErrorCode.FATIGUE_CALCULATION_FAILED: {
        "message": "疲劳损伤计算失败",
        "http_code": 500,
        "retryable": True
    },
    ErrorCode.FATIGUE_MATERIAL_NOT_FOUND: {
        "message": "材料参数未找到",
        "http_code": 404,
        "retryable": False
    },
    ErrorCode.FATIGUE_NO_CYCLES: {
        "message": "未检测到有效应力循环",
        "http_code": 500,
        "retryable": False
    },

    ErrorCode.DATABASE_CONNECTION_ERROR: {
        "message": "数据库连接失败",
        "http_code": 503,
        "retryable": True
    },
    ErrorCode.DATABASE_QUERY_ERROR: {
        "message": "数据库查询失败",
        "http_code": 500,
        "retryable": True
    },
    ErrorCode.DATABASE_INSERT_ERROR: {
        "message": "数据库插入失败",
        "http_code": 500,
        "retryable": True
    },
    ErrorCode.DATABASE_BATCH_INSERT_ERROR: {
        "message": "批量插入失败",
        "http_code": 500,
        "retryable": True
    },

    ErrorCode.RATE_LIMIT_EXCEEDED: {
        "message": "请求频率超限",
        "http_code": 429,
        "retryable": True
    },
    ErrorCode.CONCURRENT_LIMIT_EXCEEDED: {
        "message": "并发请求超限",
        "http_code": 429,
        "retryable": True
    },

    ErrorCode.AUTHENTICATION_FAILED: {
        "message": "认证失败",
        "http_code": 401,
        "retryable": False
    },
    ErrorCode.PERMISSION_DENIED: {
        "message": "权限不足",
        "http_code": 403,
        "retryable": False
    },

    ErrorCode.INTERNAL_SERVER_ERROR: {
        "message": "服务器内部错误",
        "http_code": 500,
        "retryable": True
    },
}


def get_error_info(code: int) -> Dict[str, Any]:
    return ERROR_MESSAGES.get(code, ERROR_MESSAGES[ErrorCode.INTERNAL_SERVER_ERROR])


class BusinessException(Exception):
    def __init__(self, code: int, message: str = None, details: Any = None):
        self.code = code
        self.message = message or get_error_info(code)["message"]
        self.details = details
        self.http_code = get_error_info(code)["http_code"]
        self.retryable = get_error_info(code)["retryable"]
        super().__init__(self.message)
