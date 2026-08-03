import unittest
from unittest.mock import AsyncMock, call, patch

import aiohttp

import main as bot_main


class _FakeBot:
    def __init__(self, *, start_error=None, gateway_ready_once=False):
        self.gateway_ready_once = gateway_ready_once
        self.start = AsyncMock(side_effect=start_error)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class MainReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_recreates_the_bot_after_a_bounded_delay(self):
        disconnected = _FakeBot(
            start_error=aiohttp.ClientOSError(1, "offline"),
            gateway_ready_once=True,
        )
        recovered = _FakeBot()

        with (
            patch("main.check_ffmpeg"),
            patch("main.VoiceBot", side_effect=[disconnected, recovered]),
            patch("main.asyncio.sleep", new=AsyncMock()) as sleep,
        ):
            await bot_main.main()

        disconnected.start.assert_awaited_once_with(
            bot_main.config.DISCORD_TOKEN,
            reconnect=False,
        )
        recovered.start.assert_awaited_once_with(
            bot_main.config.DISCORD_TOKEN,
            reconnect=False,
        )
        self.assertEqual(sleep.call_args_list, [call(5)])

    def test_retry_delay_is_capped_at_one_minute(self):
        self.assertEqual(bot_main._discord_retry_wait_seconds(1), 5)
        self.assertEqual(bot_main._discord_retry_wait_seconds(2), 10)
        self.assertEqual(bot_main._discord_retry_wait_seconds(10), 60)


if __name__ == "__main__":
    unittest.main()
