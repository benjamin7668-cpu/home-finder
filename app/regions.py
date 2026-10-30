"""수도권(서울·인천·경기) 시·군·구 목록. 국토부 실거래가 API의 LAWD_CD 단위."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REGION_FILE = Path(__file__).parent / "resources" / "region_codes.txt"
CAPITAL_PREFIXES = ("11", "28", "41")
# 2026-07-01 인천 행정구역 개편. 법정동코드 파일이 갱신 전이라 직접 반영한다.
# 국토부 실거래가는 과거 이력까지 새 코드로만 조회된다(2026-10 실조회로 확인).
REMOVED = {"28110", "28140", "28260"}  # 중구, 동구, 서구
ADDED = {
    "28125": "인천광역시 제물포구",
    "28155": "인천광역시 영종구",
    "28275": "인천광역시 서해구",
    "28290": "인천광역시 검단구",
}


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
            code, name = parts[0], parts[1].strip()
            if code.startswith(CAPITAL_PREFIXES) and code[5:] == "00000" and code[2:5] != "000":
                level[code[:5]] = name

    # 하위 구가 있는 시(예: 수원시 41110)는 빼고 구 단위만 남긴다.
    prefix_counts: dict[str, int] = {}
    for code in level:
        prefix_counts[code[:4]] = prefix_counts.get(code[:4], 0) + 1
    kept = {
        code: name
        for code, name in level.items()
        if not (prefix_counts[code[:4]] > 1 and code[4] == "0") and code not in REMOVED
    }
    return [Sgg(code, name) for code, name in sorted({**kept, **ADDED}.items())]
