import logging
from typing import Tuple, List, Optional, Dict, Any

import numpy as np
from scipy import signal, stats

from app.config import config
from app.error_codes import ErrorCode, BusinessException

logger = logging.getLogger(__name__)


class OrderSpectralDecomposer:
    def __init__(self):
        self.config = config.spectral_decomposition
        self.window_functions = {
            "hann": np.hanning,
            "hamming": np.hamming,
            "blackman": np.blackman,
            "kaiser": lambda n: np.kaiser(n, 14),
        }

    def compute_order_spectrum(
        self,
        order_domain_signal: np.ndarray,
        order_axis: np.ndarray,
        nperseg: Optional[int] = None,
        noverlap: Optional[float] = None,
        window: Optional[str] = None
    ) -> Tuple[np.ndarray, np.ndarray]:
        if len(order_domain_signal) == 0:
            raise BusinessException(
                ErrorCode.SPECTRAL_DECOMPOSITION_FAILED,
                "输入信号为空"
            )

        if nperseg is None:
            nperseg = self.config.fft_nperseg

        if noverlap is None:
            noverlap = self.config.fft_overlap

        if window is None:
            window = self.config.fft_window

        nperseg = min(nperseg, len(order_domain_signal))
        n_overlap = int(nperseg * noverlap)

        try:
            window_func = self.window_functions.get(window, np.hanning)
            window_data = window_func(nperseg)

            freqs, psd = signal.welch(
                order_domain_signal,
                fs=1.0 / (order_axis[1] - order_axis[0]) if len(order_axis) > 1 else 1.0,
                window=window_data,
                nperseg=nperseg,
                noverlap=n_overlap,
                scaling="density",
                detrend="constant",
                axis=0
            )

            psd = np.abs(psd)

            return freqs, psd

        except Exception as e:
            logger.error(f"阶次谱计算失败: {e}")
            raise BusinessException(
                ErrorCode.SPECTRAL_DECOMPOSITION_FAILED,
                f"谱计算失败: {str(e)}"
            )

    def _find_peaks(
        self,
        spectrum: np.ndarray,
        axis: np.ndarray,
        threshold: Optional[float] = None,
        min_distance: float = None
    ) -> Tuple[np.ndarray, np.ndarray]:
        if threshold is None:
            threshold = self.config.peak_threshold * np.max(spectrum)

        if min_distance is None:
            min_distance = len(axis) // 100

        try:
            peak_indices, peak_properties = signal.find_peaks(
                spectrum,
                height=threshold,
                distance=min_distance,
                prominence=threshold * 0.1
            )

            if len(peak_indices) == 0:
                logger.warning("未检测到峰值，使用最大幅值点")
                peak_indices = np.array([np.argmax(spectrum)])

            peak_orders = axis[peak_indices]
            peak_amplitudes = spectrum[peak_indices]

            sorted_idx = np.argsort(peak_amplitudes)[::-1]
            return peak_orders[sorted_idx], peak_amplitudes[sorted_idx]

        except Exception as e:
            logger.error(f"峰值检测失败: {e}")
            raise BusinessException(
                ErrorCode.SPECTRAL_DECOMPOSITION_FAILED,
                f"峰值检测失败: {str(e)}"
            )

    def identify_resonance_orders(
        self,
        peak_orders: np.ndarray,
        peak_amplitudes: np.ndarray,
        base_order: float,
        harmonic_count: Optional[int] = None
    ) -> Dict[str, Any]:
        if harmonic_count is None:
            harmonic_count = self.config.harmonic_count

        if len(peak_orders) == 0:
            raise BusinessException(
                ErrorCode.SPECTRAL_DECOMPOSITION_NO_PEAK,
                "没有可用的峰值数据"
            )

        harmonic_orders = []
        harmonic_amplitudes = []

        for n in range(1, harmonic_count + 1):
            target_order = n * base_order
            closest_idx = np.argmin(np.abs(peak_orders - target_order))
            closest_order = peak_orders[closest_idx]

            if np.abs(closest_order - target_order) / target_order < 0.05:
                harmonic_orders.append(float(closest_order))
                harmonic_amplitudes.append(float(peak_amplitudes[closest_idx]))
            else:
                harmonic_orders.append(float(target_order))
                harmonic_amplitudes.append(0.0)

        non_harmonic_mask = np.ones(len(peak_orders), dtype=bool)
        for h_order in harmonic_orders:
            if h_order > 0:
                close_idx = np.where(np.abs(peak_orders - h_order) / h_order < 0.05)[0]
                if len(close_idx) > 0:
                    non_harmonic_mask[close_idx[0]] = False

        resonance_orders = peak_orders[non_harmonic_mask][:10]
        resonance_amplitudes = peak_amplitudes[non_harmonic_mask][:10]

        return {
            "resonance_orders": resonance_orders.tolist(),
            "resonance_amplitudes": resonance_amplitudes.tolist(),
            "harmonic_orders": harmonic_orders,
            "harmonic_amplitudes": harmonic_amplitudes,
        }

    def extract_sidebands(
        self,
        spectrum: np.ndarray,
        axis: np.ndarray,
        carrier_order: float,
        mod_order: float,
        sideband_range: Optional[int] = None
    ) -> Dict[str, Any]:
        if sideband_range is None:
            sideband_range = self.config.sideband_range

        sideband_orders = []
        sideband_amplitudes = []

        for n in range(1, sideband_range + 1):
            for sign in [-1, 1]:
                target_order = carrier_order + sign * n * mod_order

                if target_order <= 0 or target_order > np.max(axis):
                    continue

                closest_idx = np.argmin(np.abs(axis - target_order))
                sideband_orders.append(float(axis[closest_idx]))
                sideband_amplitudes.append(float(spectrum[closest_idx]))

        return {
            "sideband_orders": sideband_orders,
            "sideband_amplitudes": sideband_amplitudes
        }

    def compute_noise_floor(
        self,
        spectrum: np.ndarray,
        percentile: Optional[float] = None
    ) -> float:
        if percentile is None:
            percentile = self.config.noise_floor_percentile

        sorted_spectrum = np.sort(spectrum)
        noise_idx = int(len(sorted_spectrum) * percentile / 100.0)
        return float(np.mean(sorted_spectrum[:max(noise_idx, 10)]))

    def compute_snr(
        self,
        spectrum: np.ndarray,
        peak_amplitude: float,
        noise_floor: float
    ) -> float:
        if noise_floor <= 0:
            noise_floor = 1e-10

        snr = 10 * np.log10(max(peak_amplitude, 1e-10) / noise_floor)
        return float(snr)

    def compute_cepstrum(
        self,
        order_domain_signal: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        try:
            spectrum = np.fft.fft(order_domain_signal)
            log_spectrum = np.log(np.abs(spectrum) + 1e-10)
            cepstrum = np.fft.ifft(log_spectrum)
            quefrency = np.arange(len(cepstrum)) / len(cepstrum)

            return quefrency, np.abs(cepstrum)

        except Exception as e:
            logger.error(f"倒谱计算失败: {e}")
            raise BusinessException(
                ErrorCode.SPECTRAL_DECOMPOSITION_FAILED,
                f"倒谱计算失败: {str(e)}"
            )

    def compute_spectral_centroid(
        self,
        spectrum: np.ndarray,
        axis: np.ndarray
    ) -> float:
        if np.sum(spectrum) == 0:
            return 0.0
        return float(np.sum(axis * spectrum) / np.sum(spectrum))

    def compute_spectral_bandwidth(
        self,
        spectrum: np.ndarray,
        axis: np.ndarray,
        centroid: float
    ) -> float:
        if np.sum(spectrum) == 0:
            return 0.0
        return float(
            np.sqrt(np.sum(((axis - centroid) ** 2) * spectrum) / np.sum(spectrum))
        )

    def compute_spectral_kurtosis(
        self,
        order_domain_signal: np.ndarray,
        window_size: int = 1024,
        overlap: float = 0.5
    ) -> Tuple[np.ndarray, np.ndarray]:
        try:
            n_overlap = int(window_size * overlap)

            _, _, stft = signal.stft(
                order_domain_signal,
                nperseg=window_size,
                noverlap=n_overlap,
                return_onesided=True
            )

            kurtosis = stats.kurtosis(np.abs(stft), axis=0, fisher=True)
            freq_axis = np.fft.fftfreq(window_size)[:window_size // 2 + 1]

            return freq_axis, kurtosis

        except Exception as e:
            logger.error(f"谱峭度计算失败: {e}")
            raise BusinessException(
                ErrorCode.SPECTRAL_DECOMPOSITION_FAILED,
                f"谱峭度计算失败: {str(e)}"
            )

    def decompose(
        self,
        order_domain_signal: np.ndarray,
        order_axis: np.ndarray,
        base_order: float,
        rpm_modulation: Optional[float] = None,
        options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        options = options or {}

        freqs, psd = self.compute_order_spectrum(
            order_domain_signal,
            order_axis,
            nperseg=options.get("nperseg"),
            noverlap=options.get("noverlap"),
            window=options.get("window")
        )

        peak_orders, peak_amplitudes = self._find_peaks(
            psd, freqs,
            threshold=options.get("peak_threshold"),
            min_distance=options.get("min_distance")
        )

        resonance_info = self.identify_resonance_orders(
            peak_orders, peak_amplitudes, base_order,
            harmonic_count=options.get("harmonic_count")
        )

        if rpm_modulation is not None and rpm_modulation > 0:
            mod_order = rpm_modulation / 60.0 / (base_order * 3000 / 60.0)
            sideband_info = self.extract_sidebands(
                psd, freqs, base_order, mod_order,
                sideband_range=options.get("sideband_range")
            )
        else:
            sideband_info = {
                "sideband_orders": [],
                "sideband_amplitudes": []
            }

        noise_floor = self.compute_noise_floor(
            psd,
            percentile=options.get("noise_percentile")
        )

        snr = self.compute_snr(
            psd,
            max(peak_amplitudes[0] if len(peak_amplitudes) > 0 else 0, 1e-10),
            noise_floor
        )

        centroid = self.compute_spectral_centroid(psd, freqs)
        bandwidth = self.compute_spectral_bandwidth(psd, freqs, centroid)

        return {
            "resonance_orders": resonance_info["resonance_orders"],
            "resonance_amplitudes": resonance_info["resonance_amplitudes"],
            "harmonic_orders": resonance_info["harmonic_orders"],
            "harmonic_amplitudes": resonance_info["harmonic_amplitudes"],
            "sideband_orders": sideband_info["sideband_orders"],
            "sideband_amplitudes": sideband_info["sideband_amplitudes"],
            "noise_floor": noise_floor,
            "snr": snr,
            "spectral_centroid": centroid,
            "spectral_bandwidth": bandwidth,
            "order_axis": freqs.tolist(),
            "spectrum": psd.tolist(),
            "peak_orders": peak_orders.tolist(),
            "peak_amplitudes": peak_amplitudes.tolist(),
        }


spectral_decomposer = OrderSpectralDecomposer()
