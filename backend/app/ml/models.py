"""Machine learning models: Random Forest, XGBoost (optional), LSTM-lite."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler

from app.core.logging import get_logger
from app.indicators.base import candles_to_df
from app.indicators.trend import ema, sma
from app.models.schemas import OHLCV, SignalType

logger = get_logger(__name__)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Feature matrix from OHLCV for ML models."""
    close = df["close"]
    feat = pd.DataFrame(index=df.index)
    feat["ret_1"] = close.pct_change(1)
    feat["ret_3"] = close.pct_change(3)
    feat["ret_7"] = close.pct_change(7)
    feat["ret_14"] = close.pct_change(14)
    feat["sma_ratio_10"] = close / sma(close, 10) - 1
    feat["sma_ratio_50"] = close / sma(close, 50) - 1
    feat["ema_ratio_21"] = close / ema(close, 21) - 1
    feat["volatility_14"] = close.pct_change().rolling(14).std()
    feat["volume_z"] = (df["volume"] - df["volume"].rolling(20).mean()) / (
        df["volume"].rolling(20).std() + 1e-9
    )
    hl_range = (df["high"] - df["low"]) / close
    feat["hl_range"] = hl_range
    feat["rsi_proxy"] = close.diff().clip(lower=0).rolling(14).mean() / (
        close.diff().abs().rolling(14).mean() + 1e-9
    )
    return feat.replace([np.inf, -np.inf], np.nan).dropna()


def build_labels(df: pd.DataFrame, horizon: int = 5, threshold: float = 0.02) -> pd.Series:
    future = df["close"].shift(-horizon) / df["close"] - 1
    labels = pd.Series(0, index=df.index)  # 0 = hold
    labels[future > threshold] = 1  # buy
    labels[future < -threshold] = -1  # sell
    return labels


class MLSignalModel:
    """Random Forest classifier with optional XGBoost upgrade."""

    def __init__(self) -> None:
        self.model: Optional[RandomForestClassifier] = None
        self.scaler = StandardScaler()
        self.trained = False
        self.feature_names: List[str] = []

    def fit(self, candles: List[OHLCV]) -> Dict[str, float]:
        df = candles_to_df(candles)
        if len(df) < 80:
            logger.warning("Not enough data to train ML model")
            return {"accuracy": 0.0}

        feat = build_features(df)
        labels = build_labels(df).reindex(feat.index).dropna()
        feat = feat.loc[labels.index]
        # Drop last horizon rows with NaN labels already handled
        X = feat.values
        y = labels.values
        # Map -1,0,1 → keep as is for RF
        split = int(len(X) * 0.8)
        X_train, X_test = X[:split], X[split:]
        y_train, y_test = y[:split], y[split:]

        self.scaler.fit(X_train)
        Xs = self.scaler.transform(X_train)
        Xts = self.scaler.transform(X_test)

        try:
            from xgboost import XGBClassifier

            # Remap labels to 0,1,2 for XGBoost
            mapping = {-1: 0, 0: 1, 1: 2}
            inv = {0: -1, 1: 0, 2: 1}
            y_tr = np.array([mapping[int(v)] for v in y_train])
            y_te = np.array([mapping[int(v)] for v in y_test])
            self.model = XGBClassifier(
                n_estimators=100,
                max_depth=4,
                learning_rate=0.08,
                subsample=0.8,
                colsample_bytree=0.8,
                objective="multi:softprob",
                num_class=3,
                verbosity=0,
            )
            self.model.fit(Xs, y_tr)
            pred = self.model.predict(Xts)
            acc = float(np.mean(pred == y_te))
            self._label_map = inv
            self._use_xgb = True
        except Exception:
            self.model = RandomForestClassifier(
                n_estimators=200, max_depth=6, min_samples_leaf=5, random_state=42, n_jobs=-1
            )
            self.model.fit(Xs, y_train)
            pred = self.model.predict(Xts)
            acc = float(np.mean(pred == y_test))
            self._use_xgb = False
            self._label_map = None

        self.feature_names = list(feat.columns)
        self.trained = True
        logger.info("ML model trained — holdout accuracy %.2f%%", acc * 100)
        return {"accuracy": acc}

    def predict(self, candles: List[OHLCV]) -> Tuple[SignalType, float]:
        if not self.trained or self.model is None:
            return SignalType.HOLD, 0.0
        df = candles_to_df(candles)
        feat = build_features(df)
        if feat.empty:
            return SignalType.HOLD, 0.0
        row = feat.iloc[[-1]].values
        Xs = self.scaler.transform(row)
        if getattr(self, "_use_xgb", False):
            proba = self.model.predict_proba(Xs)[0]
            cls = int(np.argmax(proba))
            label = self._label_map[cls]
            conf = float(proba[cls])
        else:
            proba = self.model.predict_proba(Xs)[0]
            classes = list(self.model.classes_)
            idx = int(np.argmax(proba))
            label = int(classes[idx])
            conf = float(proba[idx])

        if label == 1:
            return SignalType.BUY, conf
        if label == -1:
            return SignalType.SELL, conf
        return SignalType.HOLD, conf


class LSTMLiteModel:
    """Lightweight sequence model using sklearn MLP on lagged returns (no PyTorch required)."""

    def __init__(self, window: int = 20) -> None:
        from sklearn.neural_network import MLPClassifier

        self.window = window
        self.model = MLPClassifier(hidden_layer_sizes=(64, 32), max_iter=300, random_state=42)
        self.trained = False

    def _sequences(self, closes: np.ndarray):
        X, y = [], []
        rets = np.diff(closes) / closes[:-1]
        for i in range(self.window, len(rets) - 5):
            X.append(rets[i - self.window : i])
            fut = closes[i + 5] / closes[i] - 1
            y.append(1 if fut > 0.02 else (-1 if fut < -0.02 else 0))
        return np.array(X), np.array(y)

    def fit(self, candles: List[OHLCV]) -> Dict[str, float]:
        closes = np.array([c.close for c in candles])
        if len(closes) < self.window + 30:
            return {"accuracy": 0.0}
        X, y = self._sequences(closes)
        split = int(len(X) * 0.8)
        self.model.fit(X[:split], y[:split])
        pred = self.model.predict(X[split:])
        acc = float(np.mean(pred == y[split:])) if len(pred) else 0.0
        self.trained = True
        return {"accuracy": acc}

    def predict(self, candles: List[OHLCV]) -> Tuple[SignalType, float]:
        if not self.trained:
            return SignalType.HOLD, 0.0
        closes = np.array([c.close for c in candles])
        rets = np.diff(closes) / closes[:-1]
        if len(rets) < self.window:
            return SignalType.HOLD, 0.0
        x = rets[-self.window :].reshape(1, -1)
        proba = self.model.predict_proba(x)[0]
        classes = list(self.model.classes_)
        idx = int(np.argmax(proba))
        label = int(classes[idx])
        conf = float(proba[idx])
        if label == 1:
            return SignalType.BUY, conf
        if label == -1:
            return SignalType.SELL, conf
        return SignalType.HOLD, conf


ml_model = MLSignalModel()
lstm_model = LSTMLiteModel()
