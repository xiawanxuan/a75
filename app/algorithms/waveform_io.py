import io
import struct
import zlib
import logging
from typing import Tuple, List, Optional, Dict, Any
from datetime import datetime, timedelta

import numpy as np

from app.config import config
from app.error_codes import ErrorCode, BusinessException

logger = logging.getLogger(__name__)


class WaveformIO:
    BINARY_FORMAT = ">f4"
    HEADER_SIZE = 32

    def __init__(self):
        self.max_shard_size = config.ingestion.max_shard_size_mb * 1024 * 1024
        self.batch_size = config.ingestion.batch_size

    def parse_binary_waveform(
        self,
        binary_data: bytes,
        sample_count: int,
        compression: Optional[str] = None,
        byte_order: str = "little"
    ) -> np.ndarray:
        try:
            if compression == "gzip":
                binary_data = zlib.decompress(binary_data)
            elif compression == "lz4":
                try:
                    import lz4.frame
                    binary_data = lz4.frame.decompress(binary_data)
                except ImportError:
                    logger.warning("lz4 library not available, skipping decompression")

            expected_size = sample_count * 4
            if len(binary_data) < expected_size:
                raise BusinessException(
                    ErrorCode.WAVEFORM_FORMAT_ERROR,
                    f"波形数据长度不足: 预期 {expected_size} 字节, 实际 {len(binary_data)} 字节"
                )

            dtype = np.dtype(f"{byte_order}f4")
            waveform = np.frombuffer(binary_data[:expected_size], dtype=dtype)

            if not np.all(np.isfinite(waveform)):
                waveform = np.nan_to_num(waveform, nan=0.0, posinf=0.0, neginf=0.0)
                logger.warning("波形数据包含非有限值，已自动替换为0")

            return waveform.astype(np.float32)

        except zlib.error as e:
            logger.error(f"波形解压失败: {e}")
            raise BusinessException(
                ErrorCode.WAVEFORM_PARSE_ERROR,
                f"波形解压失败: {str(e)}"
            )
        except Exception as e:
            logger.error(f"波形解析失败: {e}")
            raise BusinessException(
                ErrorCode.WAVEFORM_PARSE_ERROR,
                f"波形解析失败: {str(e)}"
            )

    def validate_waveform(self, waveform: np.ndarray) -> bool:
        if waveform is None or len(waveform) == 0:
            raise BusinessException(ErrorCode.WAVEFORM_EMPTY, "波形数据为空")

        if len(waveform) < 100:
            logger.warning(f"波形数据点过少: {len(waveform)} 点")

        if np.std(waveform) < 1e-10:
            logger.warning("波形数据方差过小，可能为无效数据")

        return True

    def shard_waveform(
        self,
        waveform: np.ndarray,
        max_points_per_shard: int = None
    ) -> List[np.ndarray]:
        if max_points_per_shard is None:
            max_points_per_shard = int(self.max_shard_size / 4)

        if len(waveform) <= max_points_per_shard:
            return [waveform]

        shards = []
        for i in range(0, len(waveform), max_points_per_shard):
            shards.append(waveform[i:i + max_points_per_shard])

        logger.info(f"波形分片完成: {len(shards)} 个分片, 每片最多 {max_points_per_shard} 点")
        return shards

    def array_to_postgres_array(self, arr: np.ndarray) -> List[float]:
        if arr is None or len(arr) == 0:
            return []
        return arr.astype(float).tolist()

    def chunk_for_database(
        self,
        waveform: np.ndarray,
        base_time: datetime,
        sample_rate: int,
        chunk_size: int = None
    ) -> List[Dict[str, Any]]:
        if chunk_size is None:
            chunk_size = self.batch_size

        if len(waveform) == 0:
            return []

        chunks = []
        sample_interval = timedelta(seconds=1.0 / sample_rate)

        for i in range(0, len(waveform), chunk_size):
            chunk_data = waveform[i:i + chunk_size]
            chunk_time = base_time + sample_interval * i

            chunks.append({
                "time": chunk_time,
                "strain_values": self.array_to_postgres_array(chunk_data),
                "sample_count": len(chunk_data),
                "start_index": i
            })

        return chunks

    def serialize_waveform(self, waveform: np.ndarray, compression: str = None) -> bytes:
        binary_data = waveform.astype(">f4").tobytes()

        if compression == "gzip":
            return zlib.compress(binary_data, level=6)
        elif compression == "lz4":
            try:
                import lz4.frame
                return lz4.frame.compress(binary_data, compression_level=6)
            except ImportError:
                logger.warning("lz4 library not available, using no compression")
                return binary_data

        return binary_data

    def read_waveform_from_file(self, filepath: str) -> np.ndarray:
        try:
            with open(filepath, "rb") as f:
                data = f.read()

            header = data[:self.HEADER_SIZE]
            sample_count, sample_rate, _ = struct.unpack(">IIQ", header[:16])
            waveform_data = data[self.HEADER_SIZE:]

            return self.parse_binary_waveform(waveform_data, sample_count)

        except Exception as e:
            logger.error(f"从文件读取波形失败: {e}")
            raise BusinessException(
                ErrorCode.WAVEFORM_PARSE_ERROR,
                f"文件读取失败: {str(e)}"
            )

    def write_waveform_to_file(
        self,
        waveform: np.ndarray,
        filepath: str,
        sample_rate: int
    ) -> None:
        try:
            header = struct.pack(">IIQ", len(waveform), sample_rate, 0)
            data = self.serialize_waveform(waveform)

            with open(filepath, "wb") as f:
                f.write(header)
                f.write(data)

        except Exception as e:
            logger.error(f"写入波形到文件失败: {e}")
            raise BusinessException(
                ErrorCode.WAVEFORM_PARSE_ERROR,
                f"文件写入失败: {str(e)}"
            )

    def downsample_waveform(
        self,
        waveform: np.ndarray,
        original_rate: int,
        target_rate: int,
        method: str = "decimate"
    ) -> Tuple[np.ndarray, int]:
        if target_rate >= original_rate:
            return waveform, original_rate

        factor = original_rate // target_rate
        if factor * target_rate != original_rate:
            logger.warning(
                f"采样率不是整数倍: {original_rate} -> {target_rate}, "
                f"使用近似因子 {factor}"
            )

        if method == "decimate":
            from scipy import signal
            if factor <= 13:
                return signal.decimate(waveform, factor, ftype="fir"), target_rate
            else:
                result = waveform
                remaining_factor = factor
                while remaining_factor > 13:
                    result = signal.decimate(result, 10, ftype="fir")
                    remaining_factor //= 10
                return signal.decimate(result, remaining_factor, ftype="fir"), target_rate

        elif method == "mean":
            new_length = len(waveform) // factor
            return np.mean(
                waveform[:new_length * factor].reshape(new_length, factor),
                axis=1
            ), target_rate

        elif method == "max":
            new_length = len(waveform) // factor
            return np.max(
                waveform[:new_length * factor].reshape(new_length, factor),
                axis=1
            ), target_rate

        else:
            raise BusinessException(
                ErrorCode.PARAM_VALIDATION_ERROR,
                f"未知的降采样方法: {method}"
            )

    def detrend_waveform(self, waveform: np.ndarray, method: str = "linear") -> np.ndarray:
        from scipy import signal

        if method == "linear":
            return signal.detrend(waveform, type="linear")
        elif method == "constant":
            return signal.detrend(waveform, type="constant")
        else:
            return waveform - np.mean(waveform)

    def filter_waveform(
        self,
        waveform: np.ndarray,
        sample_rate: int,
        lowcut: float = None,
        highcut: float = None,
        order: int = 4
    ) -> np.ndarray:
        from scipy import signal

        nyquist = 0.5 * sample_rate

        try:
            if lowcut and highcut:
                low = lowcut / nyquist
                high = highcut / nyquist
                b, a = signal.butter(order, [low, high], btype="band")
                return signal.filtfilt(b, a, waveform)
            elif lowcut:
                low = lowcut / nyquist
                b, a = signal.butter(order, low, btype="high")
                return signal.filtfilt(b, a, waveform)
            elif highcut:
                high = highcut / nyquist
                b, a = signal.butter(order, high, btype="low")
                return signal.filtfilt(b, a, waveform)
            else:
                return waveform
        except Exception as e:
            logger.error(f"滤波失败: {e}, 返回原始波形")
            return waveform

    def convert_strain_to_stress(
        self,
        strain: np.ndarray,
        elastic_modulus_gpa: float
    ) -> np.ndarray:
        elastic_modulus_mpa = elastic_modulus_gpa * 1000.0
        return strain * elastic_modulus_mpa


waveform_io = WaveformIO()
