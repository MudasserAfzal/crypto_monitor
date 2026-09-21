"""Local JSON file storage — replaces database for persistence."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# Repo root: backend/app/core/storage.py → parents[3] = crypto/
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DATA_DIR = _REPO_ROOT / "data"


class LocalStore:
    """Thread-safe JSON file store under ``data/``."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root else _DEFAULT_DATA_DIR
        self._lock = threading.Lock()
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for sub in (
            "tickers",
            "ohlcv",
            "signals",
            "reports",
            "reports/history",
            "sentiment",
            "overview",
            "onchain",
            "universe",
            "news",
        ):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def write_json(self, relative: str, payload: Any) -> Path:
        path = self.path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with self._lock:
            tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            tmp.replace(path)
        logger.debug("Wrote %s", path)
        return path

    def read_json(self, relative: str, default: Any = None) -> Any:
        path = self.path(relative)
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read %s: %s", path, exc)
            return default

    def exists(self, relative: str) -> bool:
        return self.path(relative).exists()

    # --- domain helpers -------------------------------------------------

    def save_tickers(self, tickers: List[Dict[str, Any]], *, source: str = "aggregate") -> Path:
        stamp = datetime.now(timezone.utc).isoformat()
        payload = {
            "updated_at": stamp,
            "source": source,
            "count": len(tickers),
            "tickers": tickers,
        }
        path = self.write_json("tickers/all_tickers.json", payload)
        # Index by symbol for quick lookup
        by_sym: Dict[str, Dict[str, Any]] = {}
        for t in tickers:
            sym = (t.get("symbol") or "").upper()
            if sym:
                by_sym[sym] = t
        self.write_json("tickers/by_symbol.json", {"updated_at": stamp, "tickers": by_sym})
        # Universe list
        symbols = sorted(by_sym.keys())
        self.write_json(
            "universe/symbols.json",
            {"updated_at": stamp, "count": len(symbols), "symbols": symbols},
        )
        logger.info("Saved %d tickers → %s", len(tickers), path)
        return path

    def load_tickers(self) -> List[Dict[str, Any]]:
        data = self.read_json("tickers/all_tickers.json", {})
        return data.get("tickers") or []

    def load_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        data = self.read_json("tickers/by_symbol.json", {})
        return (data.get("tickers") or {}).get(symbol.upper())

    def load_symbols(self) -> List[str]:
        data = self.read_json("universe/symbols.json", {})
        return data.get("symbols") or []

    def save_ohlcv(self, symbol: str, timeframe: str, candles: List[Dict[str, Any]]) -> Path:
        relative = f"ohlcv/{symbol.upper()}_{timeframe}.json"
        payload = {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(candles),
            "candles": candles,
        }
        return self.write_json(relative, payload)

    def load_ohlcv(self, symbol: str, timeframe: str) -> List[Dict[str, Any]]:
        data = self.read_json(f"ohlcv/{symbol.upper()}_{timeframe}.json", {})
        return data.get("candles") or []

    def save_report(self, report: Dict[str, Any]) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.write_json(f"reports/history/{stamp}.json", report)
        path = self.write_json("reports/latest.json", report)
        # Convenience copy at data root
        self.write_json("latest_report.json", report)
        logger.info("Saved report → %s", path)
        return path

    def load_report(self) -> Optional[Dict[str, Any]]:
        return self.read_json("reports/latest.json") or self.read_json("latest_report.json")

    def save_signals(self, signals: List[Dict[str, Any]]) -> Path:
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(signals),
            "signals": signals,
        }
        by_sym = {s["symbol"].upper(): s for s in signals if s.get("symbol")}
        self.write_json("signals/by_symbol.json", {"updated_at": payload["updated_at"], "signals": by_sym})
        return self.write_json("signals/all_signals.json", payload)

    def load_signals(self) -> List[Dict[str, Any]]:
        data = self.read_json("signals/all_signals.json", {})
        return data.get("signals") or []

    def save_sentiment(self, items: List[Dict[str, Any]]) -> Path:
        return self.write_json(
            "sentiment/latest.json",
            {"updated_at": datetime.now(timezone.utc).isoformat(), "items": items},
        )

    def save_overview(self, overview: Dict[str, Any]) -> Path:
        overview = {**overview, "updated_at": datetime.now(timezone.utc).isoformat()}
        return self.write_json("overview/latest.json", overview)

    def save_news(self, items: List[Dict[str, Any]]) -> Path:
        return self.write_json(
            "news/latest.json",
            {"updated_at": datetime.now(timezone.utc).isoformat(), "count": len(items), "items": items},
        )

    def load_news(self) -> List[Dict[str, Any]]:
        data = self.read_json("news/latest.json", {})
        return data.get("items") or []

    def save_onchain(self, metrics: List[Dict[str, Any]]) -> Path:
        return self.write_json(
            "onchain/latest.json",
            {"updated_at": datetime.now(timezone.utc).isoformat(), "metrics": metrics},
        )


store = LocalStore()
