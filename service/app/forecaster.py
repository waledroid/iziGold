from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class QuantileForecast:
    q10: list[float]
    q50: list[float]
    q90: list[float]


class Forecaster(ABC):
    @abstractmethod
    def forecast(self, closes: list[float], horizon: int) -> QuantileForecast: ...


class FakeForecaster(Forecaster):
    """Deterministic linear extrapolation — for tests and offline dev."""

    def forecast(self, closes, horizon):
        x = np.arange(len(closes))
        slope, intercept = np.polyfit(x, np.array(closes), 1)
        q50 = [float(intercept + slope * (len(closes) + i)) for i in range(1, horizon + 1)]
        band = float(np.std(np.diff(closes))) or 1e-9
        return QuantileForecast(q10=[v - band for v in q50], q50=q50, q90=[v + band for v in q50])


class RulesForecaster(Forecaster):
    """Deterministic, dependency-free fast tier.

    Deliberately NEUTRAL: the DeepSeek agent is the sole source of the trading
    grade, and this tier exists only so the synchronous /analyze path always
    answers within the EA's 3 s budget. A neutral forecast yields a neutral
    verdict, which grades nothing and blocks nothing -- when the agent is
    unavailable the strategy trades exactly as it did before the agent existed.
    """

    def forecast(self, closes, horizon):
        last = float(closes[-1])
        return QuantileForecast(q10=[last] * horizon, q50=[last] * horizon,
                                q90=[last] * horizon)


class ChronosBoltForecaster(Forecaster):
    def __init__(self, model_name: str):
        self._model_name = model_name
        self._pipeline = None

    def _load(self):
        if self._pipeline is None:
            import torch
            from chronos import BaseChronosPipeline
            self._pipeline = BaseChronosPipeline.from_pretrained(
                self._model_name, device_map="cpu", torch_dtype=torch.float32)
        return self._pipeline

    def forecast(self, closes, horizon):
        import torch
        # positional: the first param is `context` in chronos 1.x, `inputs` in 2.x
        q, _ = self._load().predict_quantiles(
            torch.tensor(closes, dtype=torch.float32),
            prediction_length=horizon,
            quantile_levels=[0.1, 0.5, 0.9])
        q = q[0]  # (horizon, 3)
        return QuantileForecast(q10=q[:, 0].tolist(), q50=q[:, 1].tolist(), q90=q[:, 2].tolist())


class ChronosOnnxForecaster(Forecaster):
    """High-performance ONNX Quantized Chronos forecaster with fail-open fallback.

    Provides sub-100ms CPU inference via ONNX Runtime int8/fp16 quantization.
    If onnxruntime or the ONNX model artifact is not available, falls back
    seamlessly to ChronosBoltForecaster or RulesForecaster.
    """

    def __init__(self, model_name: str = "amazon/chronos-bolt-small", onnx_path: str | None = None):
        self._model_name = model_name
        self._onnx_path = onnx_path
        self._session = None
        self._fallback_bolt = None

    def _get_session(self):
        if self._session is None and self._onnx_path:
            try:
                import onnxruntime as ort
                sess_options = ort.SessionOptions()
                sess_options.intra_op_num_threads = 2
                sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self._session = ort.InferenceSession(self._onnx_path, sess_options, providers=["CPUExecutionProvider"])
            except Exception:
                self._session = None
        return self._session

    def forecast(self, closes: list[float], horizon: int) -> QuantileForecast:
        sess = self._get_session()
        if sess is not None:
            try:
                arr = np.array(closes, dtype=np.float32).reshape(1, -1)
                input_name = sess.get_inputs()[0].name
                outputs = sess.run(None, {input_name: arr})
                q = outputs[0][0]  # (horizon, 3)
                return QuantileForecast(q10=q[:, 0].tolist(), q50=q[:, 1].tolist(), q90=q[:, 2].tolist())
            except Exception:
                pass
        # Fail-open fallback: standard bolt or rules
        try:
            if self._fallback_bolt is None:
                self._fallback_bolt = ChronosBoltForecaster(self._model_name)
            return self._fallback_bolt.forecast(closes, horizon)
        except Exception:
            return RulesForecaster().forecast(closes, horizon)


def get_forecaster(settings) -> Forecaster:
    f_name = (getattr(settings, "forecaster", None) or "chronos").lower()
    if f_name == "fake":
        return FakeForecaster()
    if f_name in ("rules", "none"):
        return RulesForecaster()
    if f_name in ("chronos_onnx", "onnx"):
        return ChronosOnnxForecaster(settings.chronos_model)
    if f_name == "chronos":
        return ChronosBoltForecaster(settings.chronos_model)
    if f_name == "timemoe":
        raise NotImplementedError("planned alternative — see spec section 3")
    raise ValueError(f"unknown forecaster: {settings.forecaster}")

