from threading import RLock
from typing import Any
import os
import json


class SharedBoard:
    """Doc 3.1.1: global structured context all agents read/write."""

    _instance = None
    _instance_data: dict[str, Any] = {}
    _instance_lock = RLock()
    _persist_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "shared_board")

    def __new__(cls):
        """单例模式：所有实例共享同一份数据"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    # 确保持久化目录存在
                    os.makedirs(cls._persist_dir, exist_ok=True)
        return cls._instance

    def __init__(self) -> None:
        self._lock = self._instance_lock

    def _get_persist_path(self, key: str) -> str:
        """获取持久化文件路径"""
        safe_key = key.replace("/", "_").replace("\\", "_").replace(":", "_")
        return os.path.join(self._persist_dir, f"{safe_key}.json")

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._instance_data:
                return self._instance_data.get(key, default)
            # 尝试从文件加载
            persist_path = self._get_persist_path(key)
            if os.path.exists(persist_path):
                try:
                    with open(persist_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    self._instance_data[key] = data
                    return data
                except Exception:
                    pass
            return default

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._instance_data[key] = value
            # 同时持久化到文件
            persist_path = self._get_persist_path(key)
            try:
                with open(persist_path, 'w', encoding='utf-8') as f:
                    json.dump(value, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    def merge(self, patch: dict[str, Any]) -> None:
        with self._lock:
            self._instance_data.update(patch)
            # 更新所有 key 的持久化文件
            for k, v in patch.items():
                persist_path = self._get_persist_path(k)
                try:
                    with open(persist_path, 'w', encoding='utf-8') as f:
                        json.dump(v, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._instance_data)
