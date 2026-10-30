import asyncio
import time

from app.pacing import Pacer


async def test_pacer_spaces_concurrent_waits():
    pacer = Pacer(0.05)
    starts = []

    async def one():
        await pacer.wait()
        starts.append(time.monotonic())

    t0 = time.monotonic()
    await asyncio.gather(*(one() for _ in range(5)))
    # i번째 대기는 시작 후 i×간격 이전에 끝나지 않는다 (앞 대기가 늦게 깨어나도 흔들리지 않는 기준)
    assert all(s - t0 >= i * 0.05 - 0.005 for i, s in enumerate(sorted(starts)))


def test_pacer_shared_across_event_loops():
    pacer = Pacer(0.01)
    asyncio.run(pacer.wait())
    asyncio.run(pacer.wait())  # 다른 이벤트 루프에서 써도 오류가 없어야 한다
