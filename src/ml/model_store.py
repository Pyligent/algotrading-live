from __future__ import annotations

import os
import pickle
from typing import Any, Dict

from src.core.logging_utils import get_logger

logger = get_logger(__name__)


class ModelWrapper:
    def __init__(self, model_path: str):
        self.model_path = model_path
        self.model: Any | None = None
        self.metadata: Dict[str, Any] | None = None
        self.feature_names: list[str] | None = None

    def load(self) -> bool:
        if not os.path.exists(self.model_path):
            logger.error(f"ML model file not found: {self.model_path}")
            return False
        try:
            try:
                import joblib  # type: ignore

                obj = joblib.load(self.model_path)
            except Exception:
                with open(self.model_path, "rb") as f:
                    obj = pickle.load(f)
            # Support plain model or {model, metadata, feature_names}
            if isinstance(obj, dict) and "model" in obj:
                self.model = obj["model"]
                self.metadata = obj.get("metadata")
                self.feature_names = obj.get("feature_names")
            else:
                self.model = obj
                # Try to derive feature names from sklearn estimator
                if hasattr(self.model, "feature_names_in_"):
                    self.feature_names = list(getattr(self.model, "feature_names_in_"))
            logger.info(f"Loaded ML model from {self.model_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load ML model from {self.model_path}: {e}")
            self.model = None
            return False

    def predict_proba(self, features: Dict[str, float]) -> float:
        if self.model is None:
            raise RuntimeError("Model not loaded")
        # Order-independent: map dict to sorted feature order expected by model if available
        # If we have feature_names, align accordingly
        X = None
        if self.feature_names:
            X = [[features.get(n, 0.0) for n in self.feature_names]]
        else:
            # Fallback: sorted by key for stability
            keys = sorted(features.keys())
            X = [[features[k] for k in keys]]
        proba = None
        if hasattr(self.model, "predict_proba"):
            proba = self.model.predict_proba(X)[0][1]
        else:
            # Some classifiers expose decision_function or predict; map roughly to [0,1]
            if hasattr(self.model, "decision_function"):
                import math

                s = float(self.model.decision_function(X)[0])
                proba = 1.0 / (1.0 + math.exp(-s))
            else:
                pred = float(self.model.predict(X)[0])
                proba = max(0.0, min(1.0, pred))
        return float(proba)


