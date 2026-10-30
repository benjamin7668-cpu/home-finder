import asyncio
import time

from app.pacing import Pacer


async def test_pacer_spaces_concurrent_waits():
    pacer = Pacer(0.05)
    starts = []

    async def one():
        await pacer.wait()
        starts.append(time.monotonic())

    await asyncio.gather(*(one() for _ in range(5)))
    gaps = [b - a for a, b in zip(sorted(starts), sorted(starts)[1:])]
    assert min(gaps) >= 0.045


def test_pacer_shared_across_event_loops():
    pacer = Pacer(0.01)
    asyncio.run(pacer.wait())
    asyncio.run(pacer.wait())  # 다른 이벤트 루프에서 써도 오류가 없어야 한다
