"""ランタイム設定の永続化（JSONファイル）"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

import config

logger = logging.getLogger(__name__)

STATE_FILE_PATH = Path(os.getenv("RUNTIME_STATE_FILE", "data/runtime_state.json"))

_save_lock = threading.Lock()


def _to_int_key_map(data: Any, *, value_cast: type[int] | type[float] = int) -> dict[int, Any]:
    if not isinstance(data, dict):
        return {}

    result: dict[int, Any] = {}
    for key, value in data.items():
        try:
            int_key = int(key)
            casted_value = value_cast(value)
        except (TypeError, ValueError):
            continue
        result[int_key] = casted_value
    return result


def _to_bool_key_map(data: Any) -> dict[int, bool]:
    if not isinstance(data, dict):
        return {}

    result: dict[int, bool] = {}
    for key, value in data.items():
        try:
            int_key = int(key)
        except (TypeError, ValueError):
            continue

        if isinstance(value, bool):
            result[int_key] = value
            continue

        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "on", "yes"}:
                result[int_key] = True
                continue
            if normalized in {"false", "0", "off", "no"}:
                result[int_key] = False
                continue

        if isinstance(value, (int, float)):
            result[int_key] = bool(value)

    return result


def _to_nested_int_key_map(data: Any) -> dict[int, dict[int, int]]:
    if not isinstance(data, dict):
        return {}

    result: dict[int, dict[int, int]] = {}
    for guild_id, user_map in data.items():
        try:
            int_guild_id = int(guild_id)
        except (TypeError, ValueError):
            continue

        if not isinstance(user_map, dict):
            continue

        casted_user_map: dict[int, int] = {}
        for user_id, speaker_id in user_map.items():
            try:
                casted_user_map[int(user_id)] = int(speaker_id)
            except (TypeError, ValueError):
                continue
        result[int_guild_id] = casted_user_map

    return result


def _to_nested_float_key_map(data: Any) -> dict[int, dict[int, float]]:
    if not isinstance(data, dict):
        return {}

    result: dict[int, dict[int, float]] = {}
    for guild_id, user_map in data.items():
        try:
            int_guild_id = int(guild_id)
        except (TypeError, ValueError):
            continue

        if not isinstance(user_map, dict):
            continue

        casted_user_map: dict[int, float] = {}
        for user_id, speed in user_map.items():
            try:
                casted_user_map[int(user_id)] = float(speed)
            except (TypeError, ValueError):
                continue
        result[int_guild_id] = casted_user_map

    return result


def load_runtime_state() -> None:
    """設定をJSONから読み込み、configのランタイムマップへ反映する。"""
    if not STATE_FILE_PATH.exists():
        return

    try:
        data = json.loads(STATE_FILE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("ランタイム状態の読み込みに失敗しました: %s", e)
        return

    if not isinstance(data, dict):
        logger.warning("ランタイム状態ファイルの形式が不正です: %s", STATE_FILE_PATH)
        return

    config.TTS_CHANNEL_MAP.clear()
    config.TTS_CHANNEL_MAP.update(_to_int_key_map(data.get("tts_channel_map"), value_cast=int))

    config.GUILD_SPEAKER_MAP.clear()
    config.GUILD_SPEAKER_MAP.update(_to_int_key_map(data.get("guild_speaker_map"), value_cast=int))

    config.GUILD_USER_SPEAKER_MAP.clear()
    config.GUILD_USER_SPEAKER_MAP.update(_to_nested_int_key_map(data.get("guild_user_speaker_map")))

    config.GUILD_SPEED_MAP.clear()
    config.GUILD_SPEED_MAP.update(_to_int_key_map(data.get("guild_speed_map"), value_cast=float))

    config.GUILD_USER_SPEED_MAP.clear()
    config.GUILD_USER_SPEED_MAP.update(_to_nested_float_key_map(data.get("guild_user_speed_map")))

    config.GUILD_MAX_LENGTH_MAP.clear()
    config.GUILD_MAX_LENGTH_MAP.update(_to_int_key_map(data.get("guild_max_length_map"), value_cast=int))

    config.GUILD_READ_SENDER_NAME_MAP.clear()
    config.GUILD_READ_SENDER_NAME_MAP.update(_to_bool_key_map(data.get("guild_read_sender_name_map")))

    logger.info("ランタイム状態を復元しました: %s", STATE_FILE_PATH)


def _snapshot_runtime_state() -> dict[str, Any]:
    return {
        "tts_channel_map": {str(k): v for k, v in config.TTS_CHANNEL_MAP.items()},
        "guild_speaker_map": {str(k): v for k, v in config.GUILD_SPEAKER_MAP.items()},
        "guild_user_speaker_map": {
            str(guild_id): {str(user_id): speaker_id for user_id, speaker_id in user_map.items()}
            for guild_id, user_map in config.GUILD_USER_SPEAKER_MAP.items()
        },
        "guild_speed_map": {str(k): v for k, v in config.GUILD_SPEED_MAP.items()},
        "guild_user_speed_map": {
            str(guild_id): {str(user_id): speed for user_id, speed in user_map.items()}
            for guild_id, user_map in config.GUILD_USER_SPEED_MAP.items()
        },
        "guild_max_length_map": {str(k): v for k, v in config.GUILD_MAX_LENGTH_MAP.items()},
        "guild_read_sender_name_map": {
            str(k): v for k, v in config.GUILD_READ_SENDER_NAME_MAP.items()
        },
    }


def _write_state_sync(data: dict[str, Any]) -> None:
    with _save_lock:
        try:
            STATE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            temp_path = STATE_FILE_PATH.with_suffix(".tmp")
            temp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temp_path.replace(STATE_FILE_PATH)
        except Exception as e:
            logger.warning("ランタイム状態の保存に失敗しました: %s", e)


def save_runtime_state() -> None:
    """configのランタイムマップをJSONへ保存する。

    イベントループ内から呼ばれた場合は同期I/Oでループをブロックしないよう
    別スレッドに逃がす。ループ外（起動時/終了時）では同期的に書き込む。
    """
    data = _snapshot_runtime_state()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _write_state_sync(data)
        return

    loop.run_in_executor(None, _write_state_sync, data)
