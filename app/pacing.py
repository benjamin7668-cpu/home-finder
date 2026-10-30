"""외부 API 초당 한도를 넘지 않도록 요청 시작 간격을 둔다."""

from __future__ import annotations

import asyncio
import time


class Pacer:
    """요청마다 다음 시작 시각을 예약한다.

    예약(읽기·쓰기) 사이에 await가 없어 asyncio에서 원자적이므로 Lock이 필요 없고,
    모듈 전역 하나를 여러 요청·이벤트 루프가 함께 써도 된다.
    """

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._next = 0.0

    async def wait(self) -> None:
        slot = max(time.monotonic(), self._next)
        self._next = slot + self.interval
        # Windows에서는 sleep이 조금 일찍 깰 수 있어 시각이 될 때까지 반복한다
        while (delay := slot - time.monotonic()) > 0:
            await asyncio.sleep(delay)
