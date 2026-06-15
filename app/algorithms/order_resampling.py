import logging
from typing import Tuple, Optional, Dict, Any, List

import numpy as np
from scipy import interpolate, signal

from app.config import config
from app.error_codes import ErrorCode, BusinessException

logger = logging.getLogger(__name__)


class OrderResampler:
    def __init__(self):
        self.config = config.order_resampling
        self.interp_methods = {
            "linear": interpolate.interp1d,
            "cubic": interpolate.interp1d,
            "spline": interpolate.UnivariateSpline,
            "pchip": interpolate.PchipInterpolator
        }

    def _generate_order_axis(
        self,
        max_order: float,
        points_per_order: int
    ) -> np.ndarray:
        min_order = self.config.min_order
        num_points = int(max_order * points_per_order)
        return np.linspace(min_order, max_order, num_points)

    def _anti_aliasing_filter(
        self,
        signal_data: np.ndarray,
        sample_rate: float,
        cutoff_order: float,
        rpm: float
    ) -> np.ndarray:
        if not self.config.anti_aliasing_filter:
            return signal_data

        cutoff_freq = cutoff_order * rpm / 60.0
        nyquist = sample_rate / 2.0

        if cutoff_freq >= nyquist:
            return signal_data

        try:
            sos = signal.butter(
                self.config.filter_order,
                cutoff_freq / nyquist,
                btype="low",
                output="sos"
            )
            return signal.sosfiltfilt(sos, signal_data)
        except Exception as e:
            logger.warning(f"抗混叠滤波失败: {e}")
            return signal_data

    def compute_rotation_angle(
        self,
        rpm: np.ndarray,
        sample_rate: float,
        initial_angle: float = 0.0
    ) -> np.ndarray:
        if np.any(rpm <= 0):
            raise BusinessException(
                ErrorCode.ORDER_RESAMPLING_RPM_INVALID,
                "转速数据包含非正值"
            )

        dt = 1.0 / sample_rate
        angular_velocity = rpm * 2.0 * np.pi / 60.0

        angle = initial_angle + np.cumsum(angular_velocity) * dt
        return angle

    def resample_to_order_domain(
        self,
        strain_data: np.ndarray,
        rpm: np.ndarray,
        sample_rate: float,
        base_order: float = 1.0,
        max_order: Optional[float] = None,
        points_per_order: Optional[int] = None,
        method: Optional[str] = None
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
        if len(strain_data) != len(rpm):
            raise BusinessException(
                ErrorCode.ORDER_RESAMPLING_NO_DATA,
                f"数据长度不匹配: 应变 {len(strain_data)}, 转速 {len(rpm)}"
            )

        if len(strain_data) < 100:
            raise BusinessException(
                ErrorCode.ORDER_RESAMPLING_NO_DATA,
                f"数据点不足: {len(strain_data)}"
            )

        if max_order is None:
            max_order = self.config.max_order

        if points_per_order is None:
            points_per_order = self.config.resample_points_per_order

        if method is None:
            method = self.config.interpolation_method

        avg_rpm = np.mean(rpm)
        filtered_data = self._anti_aliasing_filter(
            strain_data, sample_rate, max_order * base_order, avg_rpm
        )

        angle = self.compute_rotation_angle(rpm, sample_rate)
        angle_normalized = angle / (2.0 * np.pi)

        total_revolutions = angle_normalized[-1]
        if total_revolutions < 1.0:
            raise BusinessException(
                ErrorCode.ORDER_RESAMPLING_NO_DATA,
                f"有效转数不足: {total_revolutions:.2f} 转，至少需要 1 转"
            )

        samples_per_rev = 2 * max_order * base_order * self.config.cutoff_ratio * 2
        samples_per_rev = max(int(samples_per_rev), points_per_order)
        delta_theta = 1.0 / samples_per_rev

        num_angle_samples = int(total_revolutions / delta_theta)
        if num_angle_samples < 256:
            num_angle_samples = 256
            delta_theta = total_revolutions / num_angle_samples

        angle_axis = np.arange(num_angle_samples) * delta_theta

        try:
            valid_mask = np.ones_like(angle_normalized, dtype=bool)
            if np.sum(valid_mask) < 10:
                raise BusinessException(
                    ErrorCode.ORDER_RESAMPLING_NO_DATA,
                    "有效角度范围不足"
                )

            if method in ["linear", "cubic"]:
                interp_func = self.interp_methods[method](
                    angle_normalized[valid_mask],
                    filtered_data[valid_mask],
                    kind=method,
                    fill_value="extrapolate"
                )
            elif method == "pchip":
                interp_func = self.interp_methods[method](
                    angle_normalized[valid_mask],
                    filtered_data[valid_mask]
                )
            elif method == "spline":
                interp_func = self.interp_methods[method](
                    angle_normalized[valid_mask],
                    filtered_data[valid_mask],
                    s=0.1
                )
            else:
                interp_func = self.interp_methods["linear"](
                    angle_normalized[valid_mask],
                    filtered_data[valid_mask],
                    kind="linear",
                    fill_value="extrapolate"
                )

            order_domain_signal = interp_func(angle_axis)

        except Exception as e:
            logger.error(f"阶次重采样插值失败: {e}")
            raise BusinessException(
                ErrorCode.ORDER_RESAMPLING_FAILED,
                f"插值失败: {str(e)}"
            )

        phase = np.angle(np.fft.fft(order_domain_signal))
        amplitude = np.abs(np.fft.fft(order_domain_signal - np.mean(order_domain_signal)))

        order_axis_fft = np.fft.fftfreq(len(order_domain_signal), d=delta_theta)

        return angle_axis, order_domain_signal, phase, order_axis_fft, delta_theta

    def compute_rpm_profile(
        self,
        rpm_value: float,
        num_samples: int,
        sample_rate: float,
        rpm_variation: Optional[np.ndarray] = None
    ) -> np.ndarray:
        if rpm_variation is not None and len(rpm_variation) == num_samples:
            return rpm_value + rpm_variation

        return np.full(num_samples, rpm_value, dtype=np.float64)

    def resample_time_domain(
        self,
        strain_data: np.ndarray,
        sample_rate: float,
        target_sample_rate: float
    ) -> Tuple[np.ndarray, float]:
        if target_sample_rate == sample_rate:
            return strain_data, sample_rate

        resample_ratio = target_sample_rate / sample_rate
        new_length = int(len(strain_data) * resample_ratio)

        resampled = signal.resample(strain_data, new_length)

        return resampled, target_sample_rate

    def extract_sync_pulses(
        self,
        pulse_signal: np.ndarray,
        sample_rate: float,
        threshold: float = None
    ) -> np.ndarray:
        if threshold is None:
            threshold = 0.7 * np.max(pulse_signal)

        peaks, _ = signal.find_peaks(
            pulse_signal,
            height=threshold,
            distance=int(sample_rate * 0.01)
        )

        return peaks / sample_rate

    def compute_instantaneous_rpm(
        self,
        pulse_times: np.ndarray,
        pulses_per_revolution: int = 1
    ) -> Tuple[np.ndarray, np.ndarray]:
        if len(pulse_times) < 2:
            raise BusinessException(
                ErrorCode.ORDER_RESAMPLING_RPM_INVALID,
                "脉冲数不足，无法计算转速"
            )

        intervals = np.diff(pulse_times)
        rpm = 60.0 / (intervals * pulses_per_revolution)
        rpm_times = pulse_times[:-1] + intervals / 2.0

        return rpm_times, rpm

    def interpolate_rpm_to_signal(
        self,
        rpm_times: np.ndarray,
        rpm_values: np.ndarray,
        signal_length: int,
        sample_rate: float
    ) -> np.ndarray:
        signal_times = np.arange(signal_length) / sample_rate

        try:
            interp_func = interpolate.interp1d(
                rpm_times,
                rpm_values,
                kind="linear",
                fill_value="extrapolate"
            )
            return interp_func(signal_times)
        except Exception as e:
            logger.error(f"转速插值失败: {e}")
            if len(rpm_values) > 0:
                return np.full(signal_length, np.mean(rpm_values))
            raise BusinessException(
                ErrorCode.ORDER_RESAMPLING_RPM_INVALID,
                f"转速插值失败: {str(e)}"
            )

    def process_waveform(
        self,
        strain_data: np.ndarray,
        rpm: np.ndarray,
        sample_rate: float,
        blade_count: int
    ) -> Dict[str, Any]:
        if len(strain_data) == 0:
            raise BusinessException(
                ErrorCode.ORDER_RESAMPLING_NO_DATA,
                "输入应变数据为空"
            )

        base_order = blade_count
        avg_rpm = np.mean(rpm)

        angle_axis, order_signal, phase, order_axis_fft, delta_theta = self.resample_to_order_domain(
            strain_data=strain_data,
            rpm=rpm,
            sample_rate=sample_rate,
            base_order=base_order
        )

        n = len(order_signal)
        signal_detrended = order_signal - np.mean(order_signal)
        fft_full = np.fft.fft(signal_detrended)

        amplitude_full = np.abs(fft_full)
        positive_mask = order_axis_fft >= 0

        order_axis_positive = order_axis_fft[positive_mask]
        amplitude_positive = amplitude_full[positive_mask]

        order_values = angle_axis

        return {
            "base_order": float(base_order),
            "order_values": self._to_list(order_values),
            "order_signal": self._to_list(order_signal),
            "amplitude_values": self._to_list(amplitude_positive),
            "phase_values": self._to_list(phase),
            "rpm_range": [float(np.min(rpm)), float(np.max(rpm))],
            "avg_rpm": float(avg_rpm),
            "analysis_window_seconds": float(len(strain_data) / sample_rate),
            "fft_order_axis": self._to_list(order_axis_positive),
            "fft_amplitude": self._to_list(amplitude_positive),
            "delta_theta": float(delta_theta),
            "total_revolutions": float(angle_axis[-1]) if len(angle_axis) > 0 else 0.0,
            "angle_samples": int(n)
        }

    def _to_list(self, arr: np.ndarray) -> List[float]:
        return arr.astype(float).tolist()


order_resampler = OrderResampler()
