"""ギルドごとの非同期音声再生キュー管理

改善点:
- 2段キュー構成: TTS合成ジョブキュー → WAV再生キュー
  再生中に次のメッセージのTTS合成を並走させることでレイテンシを削減
- BytesIO で直接 FFmpeg に渡すことでテンポラリファイルを廃止
"""

import asyncio
import io
import logging
from dataclasses import dataclass
from typing import Callable, Awaitable

import discord

logger = logging.getLogger(__name__)

# TTS合成タスクの型: (text, speaker_id, speed) -> bytes を返す非同期関数
TtsSynthesizer = Callable[[str, int, float], Awaitable[bytes]]


@dataclass
class TtsJob:
    """TTS合成待ちジョブ"""
    text: str
    speaker_id: int
    speed: float
    guild_id: int
    voice_client: discord.VoiceClient


@dataclass
class AudioItem:
    """再生待ちWAVデータ"""
    wav_bytes: bytes
    guild_id: int
    voice_client: discord.VoiceClient


class AudioQueueManager:
    def __init__(self, synthesizer: TtsSynthesizer) -> None:
        """
        Args:
            synthesizer: (text, speaker_id, speed) -> wav_bytes を返す非同期関数
        """
        self._synthesizer = synthesizer
        # ギルドIDごとの TTS合成ジョブキュー
        self._job_queues: dict[int, asyncio.Queue[TtsJob]] = {}
        # ギルドIDごとの WAV再生キュー
        self._play_queues: dict[int, asyncio.Queue[AudioItem]] = {}
        # ギルドIDごとのワーカータスク (合成ワーカー, 再生ワーカー)
        self._workers: dict[int, tuple[asyncio.Task, asyncio.Task]] = {}

    def _get_or_create_guild(self, guild_id: int) -> None:
        """ギルド用のキューとワーカーを初期化する（未初期化の場合のみ）"""
        if guild_id in self._workers:
            return
        self._job_queues[guild_id] = asyncio.Queue()
        self._play_queues[guild_id] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        synth_task = loop.create_task(self._synth_worker(guild_id))
        play_task = loop.create_task(self._play_worker(guild_id))
        self._workers[guild_id] = (synth_task, play_task)

    async def enqueue(
        self,
        guild_id: int,
        text: str,
        speaker_id: int,
        speed: float,
        voice_client: discord.VoiceClient,
    ) -> None:
        """TTS合成ジョブをキューに追加する"""
        self._get_or_create_guild(guild_id)
        job = TtsJob(
            text=text,
            speaker_id=speaker_id,
            speed=speed,
            guild_id=guild_id,
            voice_client=voice_client,
        )
        await self._job_queues[guild_id].put(job)

    async def _synth_worker(self, guild_id: int) -> None:
        """TTS合成ワーカー: ジョブキューを順番に合成してWAVキューへ積む"""
        job_queue = self._job_queues[guild_id]
        play_queue = self._play_queues[guild_id]
        while True:
            job: TtsJob = await job_queue.get()
            try:
                # 合成前にVoiceClientがまだ生きているか確認
                if not job.voice_client or not job.voice_client.is_connected():
                    continue
                wav_bytes = await self._synthesizer(job.text, job.speaker_id, job.speed)
                # 合成完了時にVoiceClientがまだ生きているか再確認（合成中に切断された可能性）
                if not job.voice_client.is_connected():
                    continue
                item = AudioItem(
                    wav_bytes=wav_bytes,
                    guild_id=guild_id,
                    voice_client=job.voice_client,
                )
                await play_queue.put(item)
            except Exception as e:
                logger.error("TTS合成エラー (guild=%d): %s", guild_id, e)
            finally:
                job_queue.task_done()

    async def _play_worker(self, guild_id: int) -> None:
        """再生ワーカー: WAVキューを順番に再生する"""
        play_queue = self._play_queues[guild_id]
        while True:
            item: AudioItem = await play_queue.get()
            try:
                await self._play(item)
            except Exception as e:
                logger.error("音声再生エラー (guild=%d): %s", guild_id, e)
            finally:
                play_queue.task_done()

    async def _play(self, item: AudioItem) -> None:
        """WAVバイト列をボイスチャンネルで再生し、完了まで待機する"""
        vc = item.voice_client
        if not vc or not vc.is_connected():
            return

        if vc.is_playing():
            vc.stop()

        loop = asyncio.get_running_loop()
        done_event = asyncio.Event()

        def after_play(error: Exception | None) -> None:
            if error:
                logger.error("FFmpegエラー: %s", error)
            loop.call_soon_threadsafe(done_event.set)

        # BytesIO で直接渡す（テンポラリファイル不要）
        buf = io.BytesIO(item.wav_bytes)
        source = discord.FFmpegPCMAudio(buf, pipe=True)
        try:
            vc.play(source, after=after_play)
        except Exception as e:
            logger.error("vc.play() に失敗しました (guild=%d): %s", item.guild_id, e)
            done_event.set()
            raise

        try:
            await asyncio.wait_for(done_event.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            logger.warning(
                "音声再生がタイムアウトしました。スキップします (guild=%d)", item.guild_id
            )
            # タイムアウト時はFFmpegプロセスを確実に停止してリソースを解放する
            try:
                if vc.is_connected() and vc.is_playing():
                    vc.stop()
            except Exception as e:
                logger.debug("タイムアウト後のvc.stop()でエラー (guild=%d): %s", item.guild_id, e)

    def cleanup(self, guild_id: int) -> None:
        """ギルドのキューとワーカータスクを終了する"""
        if guild_id in self._workers:
            synth_task, play_task = self._workers.pop(guild_id)
            synth_task.cancel()
            play_task.cancel()
        self._job_queues.pop(guild_id, None)
        self._play_queues.pop(guild_id, None)

    def cleanup_all(self) -> None:
        """全ギルドのキューとワーカータスクを終了する"""
        for guild_id in list(self._workers.keys()):
            self.cleanup(guild_id)

    def clear_queue(self, guild_id: int) -> None:
        """キューに溜まっている未処理の合成ジョブ・未再生の音声をクリアする"""
        for q in (
            self._job_queues.get(guild_id),
            self._play_queues.get(guild_id),
        ):
            if q is None:
                continue
            while not q.empty():
                try:
                    q.get_nowait()
                    q.task_done()
                except asyncio.QueueEmpty:
                    break
