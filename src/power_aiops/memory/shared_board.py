from threading import RLock
from typing import Any
import os
import json


class SharedBoard:
    """Doc 3.1.1: global structured context all agents read/write.

    Persistence: all keys are saved to a single _board.json file (batch write).
    Old per-key .json files are supported as a read-only backward-compat fallback.
    """

    _instance = None
    _instance_data: dict[str, Any] = {}
    _instance_lock = RLock()
    _loaded_from_disk = False
    _persist_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "shared_board")

    @classmethod
    def _board_file(cls) -> str:
        return os.path.join(cls._persist_dir, "_board.json")

    def __new__(cls):
        """单例模式：所有实例共享同一份数据"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    os.makedirs(cls._persist_dir, exist_ok=True)
        return cls._instance

    def __init__(self) -> None:
        self._lock = self._instance_lock
        self._ensure_loaded()

    # ── disk I/O ────────────────────────────────────────────────────────

    @classmethod
    def _ensure_loaded(cls) -> None:
        """Load aggregate _board.json on first access (idempotent)."""
        if cls._loaded_from_disk:
            return
        board_file = cls._board_file()
        if os.path.exists(board_file):
            try:
                with open(board_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    cls._instance_data.update(data)
            except Exception:
                pass
        cls._loaded_from_disk = True

    def _save_all(self) -> None:
        """Persist the full board as a single JSON file (atomic write)."""
        board_file = self._board_file()
        tmp = board_file + ".tmp"
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._instance_data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, board_file)  # atomic rename
        except Exception:
            pass

    @classmethod
    def _legacy_key_path(cls, key: str) -> str:
        """Path for old per-key .json file (backward compat read)."""
        safe_key = key.replace("/", "_").replace("\\", "_").replace(":", "_")
        return os.path.join(cls._persist_dir, f"{safe_key}.json")

    # ── public API ──────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            self._ensure_loaded()
            if key in self._instance_data:
                return self._instance_data.get(key, default)
            # backward compat: try old per-key file
            legacy = self._legacy_key_path(key)
            if os.path.exists(legacy):
                try:
                    with open(legacy, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    self._instance_data[key] = data
                    return data
                except Exception:
                    pass
            return default

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._instance_data[key] = value
            self._save_all()

    def merge(self, patch: dict[str, Any]) -> None:
        with self._lock:
            self._instance_data.update(patch)
            self._save_all()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._instance_data)


# ── Board key constants (canonical source) ──────────────────────────────

BOARD_KEY_OPS = "ops_output"
BOARD_KEY_SRE = "sre_output"
BOARD_KEY_CODE = "code_output"
BOARD_KEY_REPORT = "report_output"
BOARD_KEY_CODE_BLOCKED = "code_blocked"
BOARD_KEY_FENCE_MATCHED = "fence_matched"
BOARD_KEY_GRAPH_CONTEXT = "graph_rag_context"
BOARD_KEY_CODE_RESULT = "code_execution_result"
BOARD_KEY_VISUALIZATION = "visualization_data"
BOARD_KEY_EXPORT_PATH = "export_path"
