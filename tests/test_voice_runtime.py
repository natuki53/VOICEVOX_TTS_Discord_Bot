import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import config
from cogs.voice import VoiceCog


class VoiceRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.maps = (
            config.TTS_CHANNEL_MAP,
            config.GUILD_SPEAKER_MAP,
            config.GUILD_USER_SPEAKER_MAP,
            config.GUILD_SPEED_MAP,
            config.GUILD_USER_SPEED_MAP,
            config.GUILD_MAX_LENGTH_MAP,
            config.GUILD_READ_SENDER_NAME_MAP,
        )
        self.original_maps = [dict(runtime_map) for runtime_map in self.maps]

    def tearDown(self) -> None:
        for runtime_map, original in zip(
            self.maps,
            self.original_maps,
            strict=True,
        ):
            runtime_map.clear()
            runtime_map.update(original)

    def test_disconnect_clears_session_but_preserves_settings(self) -> None:
        guild_id = 1
        audio_queue = Mock()
        cog = VoiceCog(SimpleNamespace(audio_queue=audio_queue))

        config.TTS_CHANNEL_MAP[guild_id] = 10
        config.GUILD_SPEAKER_MAP[guild_id] = 3
        config.GUILD_USER_SPEAKER_MAP[guild_id] = {20: 5}
        config.GUILD_SPEED_MAP[guild_id] = 1.2
        config.GUILD_USER_SPEED_MAP[guild_id] = {20: 0.8}
        config.GUILD_MAX_LENGTH_MAP[guild_id] = 200
        config.GUILD_READ_SENDER_NAME_MAP[guild_id] = False

        with patch.object(cog, "_persist_runtime_state") as persist:
            cog._clear_guild_session(guild_id)

        audio_queue.cleanup.assert_called_once_with(guild_id)
        self.assertNotIn(guild_id, config.TTS_CHANNEL_MAP)
        self.assertEqual(3, config.GUILD_SPEAKER_MAP[guild_id])
        self.assertEqual({20: 5}, config.GUILD_USER_SPEAKER_MAP[guild_id])
        self.assertEqual(1.2, config.GUILD_SPEED_MAP[guild_id])
        self.assertEqual({20: 0.8}, config.GUILD_USER_SPEED_MAP[guild_id])
        self.assertEqual(200, config.GUILD_MAX_LENGTH_MAP[guild_id])
        self.assertFalse(config.GUILD_READ_SENDER_NAME_MAP[guild_id])
        persist.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
