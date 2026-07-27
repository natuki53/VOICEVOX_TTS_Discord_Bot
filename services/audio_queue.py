"""ギルドごとの非同期音声再生キュー管理

改善点:
- 2段キュー構成: TTS合成ジョブキュー → WAV再生キュー
  再生中に次のメッセージのTTS合成を並走させることでレイテンシを削減
- BytesIO で直接 FFmpeg に渡すことでテンポラリファイルを廃止
"""

import asyncio
import io
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

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
    def __init__(
        self,
        synthesizer: TtsSynthesizer,
        *,
        max_pending_jobs: int = 100,
        max_ready_audio: int = 20,
    ) -> None:
        """
        Args:
            synthesizer: (text, speaker_id, speed) -> wav_bytes を返す非同期関数
            max_pending_jobs: ギルドごとの未合成ジョブ上限
            max_ready_audio: ギルドごとの合成済み音声上限
        """
        if max_pending_jobs < 1 or max_ready_audio < 1:
            raise ValueError("queue sizes must be positive")

        self._synthesizer = synthesizer
        self._max_pending_jobs = max_pending_jobs
        self._max_ready_audio = max_ready_audio
        # ギルドIDごとの TTS合成ジョブキュー
        self._job_queues: dict[int, asyncio.Queue[TtsJob]] = {}
        # ギルドIDごとの WAV再生キュー
        self._play_queues: dict[int, asyncio.Queue[AudioItem]] = {}
        # ギルドIDごとのワーカータスク (合成ワーカー, 再生ワーカー)
        self._workers: dict[int, tuple[asyncio.Task, asyncio.Task]] = {}
        # cleanup時に現在の再生も停止できるようVoiceClientを保持する
        self._active_voice_clients: dict[int, discord.VoiceClient] = {}

    def _get_or_create_guild(self, guild_id: int) -> None:
        """ギルド用のキューとワーカーを初期化する（未初期化の場合のみ）"""
        if guild_id in self._workers:
            return
        self._job_queues[guild_id] = asyncio.Queue(
            maxsize=self._max_pending_jobs
        )
        self._play_queues[guild_id] = asyncio.Queue(
            maxsize=self._max_ready_audio
        )
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
        job_queue = self._job_queues[guild_id]
        if job_queue.full():
            self._discard_oldest(job_queue)
            logger.warning(
                "TTS待機キューが上限に達したため最古のメッセージを破棄しました "
                "(guild=%d, limit=%d)",
                guild_id,
                self._max_pending_jobs,
            )
        job_queue.put_nowait(job)

    @staticmethod
    def _discard_oldest(queue: asyncio.Queue) -> None:
        """満杯のキューから最古の項目を1件破棄する。"""
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        queue.task_done()

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
                if play_queue.full():
                    self._discard_oldest(play_queue)
                    logger.warning(
                        "音声再生キューが上限に達したため最古の音声を破棄しました "
                        "(guild=%d, limit=%d)",
                        guild_id,
                        self._max_ready_audio,
                    )
                play_queue.put_nowait(item)
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
            source.cleanup()
            logger.error("vc.play() に失敗しました (guild=%d): %s", item.guild_id, e)
            done_event.set()
            raise

        self._active_voice_clients[item.guild_id] = vc
        try:
            await asyncio.wait_for(done_event.wait(), timeout=60.0)
        except asyncio.CancelledError:
            self._stop_voice_client(vc)
            raise
        except asyncio.TimeoutError:
            logger.warning(
                "音声再生がタイムアウトしました。スキップします (guild=%d)", item.guild_id
            )
            self._stop_voice_client(vc)
        finally:
            if self._active_voice_clients.get(item.guild_id) is vc:
                self._active_voice_clients.pop(item.guild_id, None)

    @staticmethod
    def _stop_voice_client(vc: discord.VoiceClient) -> None:
        """再生中または一時停止中の音声プレイヤーを安全に停止する。"""
        try:
            if vc.is_playing() or vc.is_paused():
                vc.stop()
        except Exception as e:
            logger.debug("音声停止時のエラー: %s", e)

    async def wait_until_idle(self, guild_id: int, timeout: float) -> bool:
        """合成待ちと再生待ちの両キューが空になるまで待つ。"""
        job_queue = self._job_queues.get(guild_id)
        play_queue = self._play_queues.get(guild_id)
        if job_queue is None or play_queue is None:
            return True

        async def wait_for_queues() -> None:
            # 合成キュー完了時点で、生成された音声は再生キューへ追加済み。
            await job_queue.join()
            await play_queue.join()

        try:
            await asyncio.wait_for(wait_for_queues(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def cleanup(self, guild_id: int) -> None:
        """ギルドのキューとワーカータスクを終了する"""
        voice_client = self._active_voice_clients.pop(guild_id, None)
        if voice_client is not None:
            self._stop_voice_client(voice_client)

        if guild_id in self._workers:
            synth_task, play_task = self._workers.pop(guild_id)
            synth_task.cancel()
            play_task.cancel()

        self.clear_queue(guild_id)
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
