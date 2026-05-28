import os
import json
import logging
import asyncio
from typing import Any, Dict, Optional
from .bus import SpannerBus

logger = logging.getLogger("bfos.config")

class Config:
    """
    Manages lightweight, dynamic configuration parameters.
    Saves state atomically to a JSON file and publishes updates on the SpannerBus.
    """
    def __init__(self, filepath: str, bus: SpannerBus):
        self._filepath = os.path.abspath(filepath)
        self._bus = bus
        self._config_data: Dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load configuration from disk. Safely handles missing/corrupt files."""
        if not os.path.exists(self._filepath):
            logger.info("Config file '%s' not found. Starting with empty configuration.", self._filepath)
            self._config_data = {}
            return

        try:
            with open(self._filepath, "r", encoding="utf-8") as f:
                self._config_data = json.load(f)
            logger.debug("Loaded config from '%s'", self._filepath)
        except Exception as e:
            logger.error("Failed to read config from '%s': %s. Initializing empty.", self._filepath, e)
            self._config_data = {}

    def _save(self) -> None:
        """Atomically saves the configuration data to disk using a temp file."""
        dir_name = os.path.dirname(self._filepath)
        if dir_name:
            try:
                os.makedirs(dir_name, exist_ok=True)
            except Exception as e:
                logger.error("Failed to create directory '%s': %s", dir_name, e)
                return

        # Serialize to string in-memory first. This is fast and prevents thread-safety
        # issues if _config_data is mutated during disk write.
        try:
            config_str = json.dumps(self._config_data, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.exception("Failed to serialize config to JSON: %s", e)
            return

        temp_filepath = f"{self._filepath}.tmp"
        try:
            with open(temp_filepath, "w", encoding="utf-8") as f:
                f.write(config_str)
            os.replace(temp_filepath, self._filepath)
            logger.debug("Saved config to '%s'", self._filepath)
        except Exception as e:
            logger.exception("Failed to save config atomically to '%s': %s", self._filepath, e)
            if os.path.exists(temp_filepath):
                try:
                    os.remove(temp_filepath)
                except Exception:
                    pass

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a configuration parameter."""
        return self._config_data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """
        Set a configuration parameter.
        Writes atomically to disk and publishes a dynamic reload notification to the bus.
        """
        old_value = self._config_data.get(key)
        if old_value != value:
            self._config_data[key] = value
            self._save()
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    loop.create_task(self._bus.publish(
                        f"config/changed/{key}",
                        {
                            "key": key,
                            "old_value": old_value,
                            "new_value": value
                        }
                    ))
            except RuntimeError:
                # No running event loop, ignore publish reload notification
                pass

    def delete(self, key: str) -> None:
        """Delete a configuration parameter."""
        if key in self._config_data:
            del self._config_data[key]
            self._save()
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    loop.create_task(self._bus.publish(
                        f"config/deleted/{key}",
                        {
                            "key": key
                        }
                    ))
            except RuntimeError:
                # No running event loop, ignore publish deletion notification
                pass

    def all(self) -> Dict[str, Any]:
        """Return a copy of the entire configuration dictionary."""
        return self._config_data.copy()
