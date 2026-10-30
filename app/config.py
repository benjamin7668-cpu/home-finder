"""환경변수(.env) 로드와 누락 키 확인."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

APP_ROOT = Path(__file__).resolve().parent.parent
KEY_NAMES = ("DATA_GO_KR_API_KEY", "ODSAY_API_KEY", "KAKAO_REST_API_KEY", "KAKAO_JS_KEY")


@dataclass(frozen=True)
class Settings:
    data_go_kr_key: str | None
    odsay_key: str | None
    kakao_rest_key: str | None
    kakao_js_key: str | None

    def missing(self) -> list[str]:
        values = (self.data_go_kr_key, self.odsay_key, self.kakao_rest_key, self.kakao_js_key)
        return [name for name, value in zip(KEY_NAMES, values) if not value]


def load_settings(env_path: Path | None = None) -> Settings:
    path = env_path or APP_ROOT / ".env"
    file_values = dotenv_values(path) if path.exists() else {}

    def pick(name: str) -> str | None:
        value = file_values.get(name) or os.environ.get(name)
        return value.strip() if value and value.strip() else None

    return Settings(*(pick(name) for name in KEY_NAMES))
