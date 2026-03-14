"""Discord ずんだもん読み上げBot - エントリーポイント"""

import asyncio
import logging
import subprocess
import sys

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config
from services.voicevox import VoicevoxClient
from services.audio_queue import AudioQueueManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

RETRYABLE_DISCORD_ERRORS = (
    aiohttp.ClientConnectorDNSError,
    aiohttp.ClientConnectorError,
    aiohttp.ClientOSError,
    asyncio.TimeoutError,
)

COGS = [
    "cogs.voice",
    "cogs.tts",
]


def check_ffmpeg() -> None:
    """ffmpegがインストールされているか起動時に確認する"""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.error(
            "ffmpegが見つかりません。\n"
            "  macOS: brew install ffmpeg\n"
            "  Ubuntu: sudo apt install ffmpeg"
        )
        sys.exit(1)


class VoiceBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # 特権インテント（Developer Portalで要有効化）
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)

        self.voicevox: VoicevoxClient | None = None
        self.audio_queue: AudioQueueManager = AudioQueueManager()
        self._session: aiohttp.ClientSession | None = None

    async def setup_hook(self) -> None:
        """Bot起動時の初期化処理"""
        self._session = aiohttp.ClientSession()
        self.voicevox = VoicevoxClient(config.VOICEVOX_BASE_URL, self._session)

        # VOICEVOX Engine の疎通確認
        if await self.voicevox.check_health():
            logger.info("VOICEVOX Engine に接続しました: %s", config.VOICEVOX_BASE_URL)
        else:
            logger.warning(
                "VOICEVOX Engine に接続できませんでした: %s\n"
                "VOICEVOX Engineを起動してからBotを使用してください。",
                config.VOICEVOX_BASE_URL,
            )

        # Cog の読み込み
        for cog in COGS:
            await self.load_extension(cog)
            logger.info("Cog読み込み完了: %s", cog)

        # スラッシュコマンドの同期
        await self.tree.sync()
        logger.info("スラッシュコマンドを同期しました")

    async def on_ready(self) -> None:
        logger.info("Bot起動完了: %s (ID: %s)", self.user, self.user.id)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="/join で読み上げ開始",
            )
        )

    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """スラッシュコマンドのグローバルエラーハンドラ"""
        if isinstance(error, app_commands.CommandInvokeError):
            original = error.original
            # インタラクションのトークン切れは無視（ネットワーク遅延等で発生）
            if isinstance(original, discord.NotFound) and original.code == 10062:
                logger.warning("インタラクショントークン切れ: %s", interaction.command)
                return
        logger.error("コマンドエラー: %s", error)

    async def close(self) -> None:
        """Bot終了時のクリーンアップ"""
        self.audio_queue.cleanup_all()
        if self._session:
            await self._session.close()
        await super().close()


async def main() -> None:
    check_ffmpeg()
    retry_count = 0
    while True:
        bot = VoiceBot()
        try:
            async with bot:
                await bot.start(config.DISCORD_TOKEN)
            return
        except discord.LoginFailure:
            logger.error("DISCORD_TOKENが無効です。環境変数を確認してください。")
            return
        except RETRYABLE_DISCORD_ERRORS as e:
            retry_count += 1
            wait_seconds = min(60, 5 * (2 ** min(retry_count - 1, 4)))
            logger.warning(
                "Discord接続に失敗しました (%s): %s / %d秒後に再試行します。",
                e.__class__.__name__,
                e,
                wait_seconds,
            )
            await asyncio.sleep(wait_seconds)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Botを停止しました。")
