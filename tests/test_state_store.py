import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from services import state_store


class DeferredExecutorLoop:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def run_in_executor(self, executor, function, *args):
        del executor
        self.calls.append((function, args))


class StateStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_tts_channels = dict(config.TTS_CHANNEL_MAP)

    def tearDown(self) -> None:
        config.TTS_CHANNEL_MAP.clear()
        config.TTS_CHANNEL_MAP.update(self.original_tts_channels)

    def test_out_of_order_executor_cannot_overwrite_latest_state(self) -> None:
        loop = DeferredExecutorLoop()

        with tempfile.TemporaryDirectory() as temporary_directory:
            state_path = Path(temporary_directory) / "runtime_state.json"
            with (
                patch.object(state_store, "STATE_FILE_PATH", state_path),
                patch.object(
                    state_store.asyncio,
                    "get_running_loop",
                    return_value=loop,
                ),
            ):
                config.TTS_CHANNEL_MAP.clear()
                config.TTS_CHANNEL_MAP[1] = 10
                state_store.save_runtime_state()

                config.TTS_CHANNEL_MAP[1] = 20
                state_store.save_runtime_state()

                # 新しい保存を先に、古い保存を後に実行して競合を再現する。
                for function, args in reversed(loop.calls):
                    function(*args)

            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual({"1": 20}, saved["tts_channel_map"])


if __name__ == "__main__":
    unittest.main()
