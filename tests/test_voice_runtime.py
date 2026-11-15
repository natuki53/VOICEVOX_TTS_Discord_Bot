import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord.ext import commands

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


class VoiceConnectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.original_channels = dict(config.TTS_CHANNEL_MAP)
        self.guild = SimpleNamespace(id=1, voice_client=None)
        self.bot = SimpleNamespace(
            voice_clients=[],
            audio_queue=Mock(),
            voicevox=None,
        )
        self.cog = VoiceCog(self.bot)
        self.cog._get_bot_voice_channel = Mock(return_value=None)
        self.cog._persist_runtime_state = Mock()
        self.cog._defer_once = AsyncMock(return_value=True)
        self.cog._send_notice = AsyncMock()
        self.cog._send_once = AsyncMock()
        self.cog._refresh_speaker_cache = AsyncMock()
        self.cog._speak = AsyncMock()
        self.channel = SimpleNamespace(name="voice", connect=AsyncMock())

    def tearDown(self) -> None:
        self.cog.cog_unload()
        config.TTS_CHANNEL_MAP.clear()
        config.TTS_CHANNEL_MAP.update(self.original_channels)

    def register_voice_client(self, *, connected=False):
        vc = Mock(guild=self.guild, channel=self.channel)
        vc.is_connected.return_value = connected

        def cleanup(**kwargs):
            if vc in self.bot.voice_clients:
                self.bot.voice_clients.remove(vc)
            if self.guild.voice_client is vc:
                self.guild.voice_client = None

        vc.cleanup.side_effect = cleanup
        vc.disconnect = AsyncMock(side_effect=cleanup)
        self.bot.voice_clients.append(vc)
        self.guild.voice_client = vc
        return vc

    def interaction(self):
        return SimpleNamespace(
            guild=self.guild,
            user=SimpleNamespace(id=20, voice=SimpleNamespace(channel=self.channel)),
            channel=SimpleNamespace(mention="<#10>"),
            channel_id=10,
        )

    async def test_stale_client_is_removed_even_when_bot_is_not_in_voice(self):
        stale = self.register_voice_client()

        self.assertIsNone(await self.cog._get_or_recover_vc(self.guild))

        stale.disconnect.assert_awaited_once_with(force=True)
        self.assertIsNone(self.guild.voice_client)
        self.assertEqual([], self.bot.voice_clients)
        self.bot.audio_queue.cleanup.assert_called_once_with(self.guild.id)

    async def test_failed_disconnect_still_removes_the_cached_client(self):
        stale = self.register_voice_client()
        stale.disconnect.side_effect = OSError("voice socket already closed")

        self.assertIsNone(await self.cog._get_or_recover_vc(self.guild))

        stale.cleanup.assert_called_once_with()
        self.assertIsNone(self.guild.voice_client)

    async def test_connected_client_is_reused_without_cleanup(self):
        vc = self.register_voice_client(connected=True)

        self.assertIs(vc, await self.cog._get_or_recover_vc(self.guild))

        vc.disconnect.assert_not_awaited()
        vc.cleanup.assert_not_called()

    async def test_stale_cleanup_preserves_other_guild_connections(self):
        other = Mock(guild=SimpleNamespace(id=2))
        self.bot.voice_clients.append(other)
        self.register_voice_client()

        await self.cog._get_or_recover_vc(self.guild)

        self.assertEqual([other], self.bot.voice_clients)
        other.disconnect.assert_not_called()
        other.cleanup.assert_not_called()

    async def test_real_discord_cache_allows_connect_after_failed_disconnect(self):
        intents = discord.Intents.default()
        intents.message_content = True
        async with commands.Bot(command_prefix="!", intents=intents) as bot:
            bot.audio_queue = Mock()
            guild = discord.Guild(data={"id": "1", "name": "test"}, state=bot._connection)
            channel = discord.VoiceChannel(
                guild=guild,
                state=bot._connection,
                data={
                    "id": "2", "name": "voice", "type": 2,
                    "position": 0, "bitrate": 64000, "user_limit": 0,
                },
            )
            stale = discord.VoiceClient(bot, channel)
            bot._connection._add_voice_client(guild.id, stale)
            cog = VoiceCog(bot)

            with patch.object(
                stale, "disconnect", new=AsyncMock(side_effect=OSError("closed socket"))
            ):
                self.assertIsNone(await cog._get_or_recover_vc(guild))

            self.assertIsNone(guild.voice_client)
            with patch.object(discord.VoiceClient, "connect", new=AsyncMock()):
                recovered = await channel.connect(timeout=15.0, reconnect=True)
            self.assertIs(recovered, guild.voice_client)
            recovered.cleanup()

    async def test_join_can_reconnect_after_a_stale_client(self):
        self.register_voice_client()

        async def connect(**kwargs):
            if self.guild.voice_client is not None:
                raise discord.ClientException("Already connected to a voice channel.")
            return self.register_voice_client(connected=True)

        self.channel.connect.side_effect = connect
        await VoiceCog.join.callback(self.cog, self.interaction())

        self.channel.connect.assert_awaited_once_with(timeout=15.0, reconnect=True)
        self.assertEqual(10, config.TTS_CHANNEL_MAP.get(self.guild.id))
        self.cog._send_once.assert_awaited_once()
        self.cog._send_notice.assert_not_awaited()

    async def test_concurrent_joins_share_one_connection(self):
        connecting = asyncio.Event()
        release = asyncio.Event()

        async def connect(**kwargs):
            if self.guild.voice_client is not None:
                raise discord.ClientException("Already connected to a voice channel.")
            vc = self.register_voice_client()
            connecting.set()
            await release.wait()
            vc.is_connected.return_value = True
            return vc

        self.channel.connect.side_effect = connect
        first = asyncio.create_task(VoiceCog.join.callback(self.cog, self.interaction()))
        try:
            await asyncio.wait_for(connecting.wait(), timeout=1)
            second = asyncio.create_task(VoiceCog.join.callback(self.cog, self.interaction()))
            await asyncio.sleep(0)
        finally:
            release.set()
        await asyncio.gather(first, second)

        self.channel.connect.assert_awaited_once_with(timeout=15.0, reconnect=True)
        self.cog._send_notice.assert_awaited_once()
        self.assertIn("すでに", self.cog._send_notice.call_args.kwargs["title"])


if __name__ == "__main__":
    unittest.main()
