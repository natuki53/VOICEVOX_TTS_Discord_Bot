"""VOICEVOX Engine HTTPクライアント（非同期・aiohttp使用）"""

import aiohttp

TIMEOUT = aiohttp.ClientTimeout(total=10)


class VoicevoxError(Exception):
    """VOICEVOX APIとの通信エラー"""


class VoicevoxClient:
    def __init__(self, base_url: str, session: aiohttp.ClientSession) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = session

    async def audio_query(self, text: str, speaker_id: int) -> dict:
        """テキストからAudioQueryを生成する"""
        url = f"{self._base_url}/audio_query"
        params = {"text": text, "speaker": speaker_id}
        async with self._session.post(url, params=params, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise VoicevoxError(
                    f"audio_query failed: HTTP {resp.status} - {body}"
                )
            return await resp.json()

    async def synthesize(self, query: dict, speaker_id: int) -> bytes:
        """AudioQueryからWAV音声を合成する"""
        url = f"{self._base_url}/synthesis"
        params = {"speaker": speaker_id}
        headers = {"Content-Type": "application/json"}
        async with self._session.post(
            url, params=params, json=query, headers=headers, timeout=TIMEOUT
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise VoicevoxError(
                    f"synthesis failed: HTTP {resp.status} - {body}"
                )
            return await resp.read()

    async def tts(self, text: str, speaker_id: int, speed: float = 1.0) -> bytes:
        """テキストをWAVバイト列に変換する（audio_query → synthesis）"""
        query = await self.audio_query(text, speaker_id)
        query["speedScale"] = speed
        return await self.synthesize(query, speaker_id)

    async def check_health(self) -> bool:
        """VOICEVOX Engineが起動しているか確認する"""
        try:
            url = f"{self._base_url}/version"
            async with self._session.get(url, timeout=TIMEOUT) as resp:
                return resp.status == 200
        except Exception:
            return False
