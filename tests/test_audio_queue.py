import asyncio
import unittest
from unittest.mock import Mock

from services.audio_queue import AudioQueueManager


async def unused_synthesizer(text: str, speaker_id: int, speed: float) -> bytes:
    del text, speaker_id, speed
    return b""


class AudioQueueManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_until_idle_waits_for_synthesis_then_playback(self) -> None:
        manager = AudioQueueManager(unused_synthesizer)
        job_queue: asyncio.Queue[object] = asyncio.Queue()
        play_queue: asyncio.Queue[object] = asyncio.Queue()
        manager._job_queues[1] = job_queue
        manager._play_queues[1] = play_queue

        await job_queue.put(object())
        await play_queue.put(object())

        waiter = asyncio.create_task(manager.wait_until_idle(1, timeout=1.0))
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())

        job_queue.get_nowait()
        job_queue.task_done()
        await asyncio.sleep(0)
        self.assertFalse(waiter.done())

        play_queue.get_nowait()
        play_queue.task_done()
        self.assertTrue(await waiter)

    async def test_enqueue_drops_oldest_job_at_capacity(self) -> None:
        manager = AudioQueueManager(
            unused_synthesizer,
            max_pending_jobs=1,
        )
        manager._job_queues[1] = asyncio.Queue(maxsize=1)
        manager._play_queues[1] = asyncio.Queue()
        manager._workers[1] = (Mock(), Mock())
        voice_client = Mock()

        await manager.enqueue(1, "old", 1, 1.0, voice_client)
        await manager.enqueue(1, "new", 1, 1.0, voice_client)

        queued = manager._job_queues[1].get_nowait()
        self.assertEqual("new", queued.text)

    def test_cleanup_stops_active_playback_and_cancels_workers(self) -> None:
        manager = AudioQueueManager(unused_synthesizer)
        voice_client = Mock()
        voice_client.is_playing.return_value = True
        voice_client.is_paused.return_value = False
        synth_task = Mock()
        play_task = Mock()

        manager._active_voice_clients[1] = voice_client
        manager._job_queues[1] = asyncio.Queue()
        manager._play_queues[1] = asyncio.Queue()
        manager._workers[1] = (synth_task, play_task)

        manager.cleanup(1)

        voice_client.stop.assert_called_once_with()
        synth_task.cancel.assert_called_once_with()
        play_task.cancel.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
