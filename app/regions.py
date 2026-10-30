"""수도권(서울·인천·경기) 시·군·구 목록. 국토부 실거래가 API의 LAWD_CD 단위."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REGION_FILE = Path(__file__).parent / "resources" / "region_codes.txt"
CAPITAL_PREFIXES = ("11", "28", "41")


@dataclass(frozen=True)
class Sgg:
    code: str  # 5자리
    name: str  # 예: "경기도 성남시 분당구"


@lru_cache(maxsize=1)
def capital_area_sggs() -> list[Sgg]:
    level: dict[str, str] = {}
    with REGION_FILE.open(encoding="utf-8") as f:
        next(f)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or parts[2] != "존재":
                continue
            code, name = parts[0], parts[1]
            if code.startswith(CAPITAL_PREFIXES) and code[5:] == "00000" and code[2:5] != "000":
                level[code[:5]] = name

    # 하위 구가 있는 시(예: 수원시 41110)는 빼고 구 단위만 남긴다.
    prefix_counts: dict[str, int] = {}
    for code in level:
        prefix_counts[code[:4]] = prefix_counts.get(code[:4], 0) + 1
    return [
        Sgg(code, name)
        for code, name in sorted(level.items())
        if not (prefix_counts[code[:4]] > 1 and code[4] == "0")
    ]
