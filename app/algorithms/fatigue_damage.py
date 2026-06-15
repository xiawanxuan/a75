import logging
from typing import Tuple, List, Optional, Dict, Any

import numpy as np

from app.config import config
from app.error_codes import ErrorCode, BusinessException

logger = logging.getLogger(__name__)


class FatigueDamageCalculator:
    def __init__(self):
        self.config = config.fatigue_damage

    def apply_mean_stress_correction(
        self,
        stress_amplitude: np.ndarray,
        mean_stress: np.ndarray,
        ultimate_strength: float,
        method: Optional[str] = None
    ) -> np.ndarray:
        if method is None:
            method = self.config.mean_stress_correction

        method = method.lower()

        if method == "goodman":
            correction = 1.0 - (mean_stress / ultimate_strength)
            correction = np.clip(correction, 1e-10, None)
            return stress_amplitude / correction

        elif method == "gerber":
            correction = 1.0 - (mean_stress / ultimate_strength) ** 2
            correction = np.clip(correction, 1e-10, None)
            return stress_amplitude / correction

        elif method == "soderberg":
            correction = 1.0 - (mean_stress / (ultimate_strength * 0.7))
            correction = np.clip(correction, 1e-10, None)
            return stress_amplitude / correction

        elif method == "morrow":
            fatigue_limit = self.config.fatigue_limit_stress
            correction = 1.0 - (mean_stress / ultimate_strength)
            corrected_fatigue_limit = fatigue_limit * np.clip(correction, 1e-10, None)
            return np.maximum(stress_amplitude, corrected_fatigue_limit)

        else:
            logger.warning(f"未知的平均应力修正方法: {method}, 使用Goodman")
            return self.apply_mean_stress_correction(
                stress_amplitude, mean_stress, ultimate_strength, "goodman"
            )

    def rainflow_counting(
        self,
        stress_history: np.ndarray,
        bins: Optional[int] = None
    ) -> Dict[str, np.ndarray]:
        if bins is None:
            bins = self.config.rainflow_bins

        try:
            from scipy import signal

            stress_history = np.asarray(stress_history)
            if len(stress_history) < 4:
                raise BusinessException(
                    ErrorCode.FATIGUE_NO_CYCLES,
                    f"应力序列太短: {len(stress_history)} 点"
                )

            stress_history = signal.detrend(stress_history, type="constant")

            cycles = []
            peaks = []
            troughs = []

            i = 0
            while i < len(stress_history) - 1:
                if stress_history[i] < stress_history[i + 1]:
                    while i < len(stress_history) - 1 and stress_history[i] < stress_history[i + 1]:
                        i += 1
                    peaks.append((i, stress_history[i]))
                else:
                    while i < len(stress_history) - 1 and stress_history[i] > stress_history[i + 1]:
                        i += 1
                    troughs.append((i, stress_history[i]))

            extrema_indices = sorted(peaks + troughs, key=lambda x: x[0])
            extrema_values = np.array([e[1] for e in extrema_indices])

            if len(extrema_values) < 3:
                raise BusinessException(
                    ErrorCode.FATIGUE_NO_CYCLES,
                    "极值点不足，无法进行雨流计数"
                )

            points = extrema_values.tolist()
            cycle_count = []

            while len(points) >= 3:
                S1 = abs(points[2] - points[1])
                S2 = abs(points[1] - points[0])

                if S2 <= S1 and len(points) >= 4:
                    S = abs(points[2] - points[1])
                    mean_stress = (points[2] + points[1]) / 2
                    cycle_count.append((S, mean_stress, 0.5))
                    del points[1:3]
                else:
                    S = abs(points[1] - points[0])
                    mean_stress = (points[1] + points[0]) / 2
                    cycle_count.append((S, mean_stress, 0.5))
                    del points[0]

            for i in range(len(points) - 1):
                S = abs(points[i + 1] - points[i])
                mean_stress = (points[i + 1] + points[i]) / 2
                cycle_count.append((S, mean_stress, 0.5))

            amplitudes = np.array([c[0] / 2 for c in cycle_count])
            means = np.array([c[1] for c in cycle_count])
            counts = np.array([c[2] for c in cycle_count])

            valid = amplitudes > 1e-10
            amplitudes = amplitudes[valid]
            means = means[valid]
            counts = counts[valid]

            if len(amplitudes) == 0:
                raise BusinessException(
                    ErrorCode.FATIGUE_NO_CYCLES,
                    "雨流计数未检测到有效循环"
                )

            amp_bins = np.linspace(0, np.max(amplitudes) * 1.1, bins + 1)
            digitized = np.digitize(amplitudes, amp_bins)

            binned_amplitudes = []
            binned_counts = []
            binned_means = []

            for i in range(1, len(amp_bins)):
                mask = digitized == i
                if np.sum(mask) > 0:
                    binned_amplitudes.append(np.mean(amplitudes[mask]))
                    binned_counts.append(np.sum(counts[mask]))
                    binned_means.append(np.mean(means[mask]))

            return {
                "stress_amplitudes": np.array(binned_amplitudes),
                "mean_stresses": np.array(binned_means),
                "cycle_counts": np.array(binned_counts),
                "total_cycles": int(np.sum(counts)),
                "raw_cycles": cycle_count
            }

        except ImportError:
            logger.error("scipy不可用，无法进行雨流计数")
            raise BusinessException(
                ErrorCode.FATIGUE_CALCULATION_FAILED,
                "scipy dependency not available"
            )
        except BusinessException:
            raise
        except Exception as e:
            logger.error(f"雨流计数失败: {e}")
            raise BusinessException(
                ErrorCode.FATIGUE_CALCULATION_FAILED,
                f"雨流计数失败: {str(e)}"
            )

    def compute_cycles_to_failure(
        self,
        stress_amplitude: float,
        sn_slope: Optional[float] = None,
        sn_intercept: Optional[float] = None,
        fatigue_limit: Optional[float] = None
    ) -> float:
        if sn_slope is None:
            sn_slope = self.config.sn_curve_slope

        if sn_intercept is None:
            sn_intercept = self.config.sn_curve_intercept

        if fatigue_limit is None:
            fatigue_limit = self.config.fatigue_limit_stress

        if stress_amplitude <= fatigue_limit:
            return 1e12

        if stress_amplitude <= 0:
            return 1e12

        cycles = 10 ** (sn_intercept - sn_slope * np.log10(stress_amplitude))

        return max(cycles, 1.0)

    def compute_miners_damage(
        self,
        stress_amplitudes: np.ndarray,
        cycle_counts: np.ndarray,
        sn_params: Optional[Dict[str, float]] = None
    ) -> float:
        sn_params = sn_params or {}

        total_damage = 0.0
        for amp, count in zip(stress_amplitudes, cycle_counts):
            cycles_to_failure = self.compute_cycles_to_failure(
                amp,
                sn_slope=sn_params.get("sn_slope"),
                sn_intercept=sn_params.get("sn_intercept"),
                fatigue_limit=sn_params.get("fatigue_limit")
            )
            total_damage += count / cycles_to_failure

        return float(total_damage)

    def compute_remaining_life(
        self,
        damage: float,
        operation_hours: float,
        design_life_hours: float,
        damage_rate_window: float = 1.0
    ) -> float:
        if damage <= self.config.damage_threshold:
            return design_life_hours

        if operation_hours <= 0:
            return design_life_hours

        damage_rate = damage / damage_rate_window
        if damage_rate <= 0:
            return design_life_hours

        remaining_damage_capacity = 1.0 - damage
        if remaining_damage_capacity <= 0:
            return 0.0

        remaining_hours = remaining_damage_capacity / damage_rate
        return float(max(remaining_hours, 0.0))

    def compute_stress_statistics(
        self,
        stress_history: np.ndarray
    ) -> Dict[str, float]:
        if len(stress_history) == 0:
            return {
                "max_stress": 0.0,
                "min_stress": 0.0,
                "mean_stress": 0.0,
                "stress_amplitude": 0.0,
                "std_stress": 0.0,
                "rms_stress": 0.0
            }

        return {
            "max_stress": float(np.max(stress_history)),
            "min_stress": float(np.min(stress_history)),
            "mean_stress": float(np.mean(stress_history)),
            "stress_amplitude": float((np.max(stress_history) - np.min(stress_history)) / 2),
            "std_stress": float(np.std(stress_history)),
            "rms_stress": float(np.sqrt(np.mean(stress_history ** 2)))
        }

    def calculate(
        self,
        stress_history: np.ndarray,
        material_params: Dict[str, Any],
        operation_hours: float = 1.0,
        accumulated_damage: float = 0.0,
        design_life_hours: float = 100000.0,
        options: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        options = options or {}

        if len(stress_history) == 0:
            raise BusinessException(
                ErrorCode.FATIGUE_NO_CYCLES,
                "应力历史数据为空"
            )

        ultimate_strength = material_params.get(
            "ultimate_strength_mpa",
            self.config.ultimate_tensile_strength
        )

        rainflow_result = self.rainflow_counting(
            stress_history,
            bins=options.get("rainflow_bins")
        )

        corrected_amplitudes = self.apply_mean_stress_correction(
            rainflow_result["stress_amplitudes"],
            rainflow_result["mean_stresses"],
            ultimate_strength,
            method=options.get("mean_stress_correction")
        )

        damage = self.compute_miners_damage(
            corrected_amplitudes,
            rainflow_result["cycle_counts"],
            sn_params=material_params
        )

        stats = self.compute_stress_statistics(stress_history)

        total_damage = accumulated_damage + damage

        remaining_life = self.compute_remaining_life(
            damage,
            operation_hours,
            design_life_hours,
            damage_rate_window=operation_hours
        )

        return {
            "damage_value": damage,
            "damage_accumulated": total_damage,
            "remaining_life_hours": remaining_life,
            "cycle_count": rainflow_result["total_cycles"],
            "max_stress": stats["max_stress"],
            "min_stress": stats["min_stress"],
            "mean_stress": stats["mean_stress"],
            "stress_amplitude": stats["stress_amplitude"],
            "std_stress": stats["std_stress"],
            "rms_stress": stats["rms_stress"],
            "rainflow_cycles": [
                {
                    "amplitude": float(amp),
                    "mean_stress": float(mean),
                    "count": float(count)
                }
                for amp, mean, count in zip(
                    rainflow_result["stress_amplitudes"],
                    rainflow_result["mean_stresses"],
                    rainflow_result["cycle_counts"]
                )
            ]
        }


fatigue_calculator = FatigueDamageCalculator()
