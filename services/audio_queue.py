"""ギルドごとの非同期音声再生キュー管理"""

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass

import discord

logger = logging.getLogger(__name__)


@dataclass
class AudioItem:
    wav_bytes: bytes
    guild_id: int
    voice_client: discord.VoiceClient


class AudioQueueManager:
    def __init__(self) -> None:
        self._queues: dict[int, asyncio.Queue[AudioItem]] = {}
        self._workers: dict[int, asyncio.Task] = {}

    def get_or_create_queue(self, guild_id: int) -> asyncio.Queue:
        if guild_id not in self._queues:
            self._queues[guild_id] = asyncio.Queue()
            self._workers[guild_id] = asyncio.get_running_loop().create_task(
                self._worker(guild_id)
            )
        return self._queues[guild_id]

    async def enqueue(
        self,
        guild_id: int,
        wav_bytes: bytes,
        voice_client: discord.VoiceClient,
    ) -> None:
        """音声データをキューに追加する"""
        q = self.get_or_create_queue(guild_id)
        item = AudioItem(wav_bytes=wav_bytes, guild_id=guild_id, voice_client=voice_client)
        await q.put(item)

    async def _worker(self, guild_id: int) -> None:
        """ギルドのキューを順番に処理するワーカータスク"""
        q = self._queues[guild_id]
        while True:
            item: AudioItem = await q.get()
            try:
                await self._play(item)
            except Exception as e:
                logger.error("音声再生エラー (guild=%d): %s", guild_id, e)
            finally:
                q.task_done()

    async def _play(self, item: AudioItem) -> None:
        """WAVバイト列をボイスチャンネルで再生し、完了まで待機する"""
        vc = item.voice_client
        if not vc or not vc.is_connected():
            return

        # 再生中の音声がある場合はスキップ（キューで順番制御しているので通常は起きない）
        if vc.is_playing():
            vc.stop()

        loop = asyncio.get_running_loop()
        done_event = asyncio.Event()
        tmp_path: str | None = None

        try:
            # WAVをテンポラリファイルに書き出す
            with tempfile.NamedTemporaryFile(
                suffix=".wav", delete=False, prefix="voicebot_"
            ) as f:
                f.write(item.wav_bytes)
                tmp_path = f.name

            def after_play(error: Exception | None) -> None:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                if error:
                    logger.error("FFmpegエラー: %s", error)
                loop.call_soon_threadsafe(done_event.set)

            source = discord.FFmpegPCMAudio(tmp_path)
            vc.play(source, after=after_play)

            # 再生完了を非同期で待機
            await done_event.wait()

        except Exception:
            # テンポラリファイルのクリーンアップ
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            raise

    def cleanup(self, guild_id: int) -> None:
        """ギルドのキューとワーカータスクを終了する"""
        if guild_id in self._workers:
            self._workers[guild_id].cancel()
            del self._workers[guild_id]
        if guild_id in self._queues:
            del self._queues[guild_id]

    def cleanup_all(self) -> None:
        """全ギルドのキューとワーカータスクを終了する"""
        for guild_id in list(self._workers.keys()):
            self.cleanup(guild_id)

    def clear_queue(self, guild_id: int) -> None:
        """キューに溜まっている未再生の音声をクリアする"""
        if guild_id in self._queues:
            q = self._queues[guild_id]
            while not q.empty():
                try:
                    q.get_nowait()
                    q.task_done()
                except asyncio.QueueEmpty:
                    break
