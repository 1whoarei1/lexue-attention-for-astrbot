from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True, slots=True)
class AppConfig:
    calendar_url: str = ""
    lexue_base_url: str = "https://lexue.bit.edu.cn"
    username: str = ""
    password: str = ""
    state_path: str = "data/state.json"


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        return AppConfig()

    raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    lexue = raw.get("lexue", {})
    auth = raw.get("auth", {})
    state = raw.get("state", {})
    return AppConfig(
        calendar_url=str(lexue.get("calendar_url", "")),
        lexue_base_url=str(lexue.get("base_url", "https://lexue.bit.edu.cn")),
        username=str(auth.get("username", "")),
        password=str(auth.get("password", "")),
        state_path=str(state.get("path", "data/state.json")),
    )
