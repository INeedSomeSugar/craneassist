from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Settings:
    root: Path
    api_key: str = field(default="", repr=False)
    base_url: str = "https://api.deepseek.com"
    model: str = ""
    timeout: float = 90
    max_turns: int = 18
    token_budget: int = 180_000

    @classmethod
    def from_env(cls, root: Path | None = None) -> Settings:
        root = (root or Path.cwd()).resolve()
        env_path = root / ".env"
        if env_path.is_file():
            for line in env_path.read_text("utf-8-sig").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    if key.strip() in {"DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"}:
                        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
        base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        parsed = urlsplit(base)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("DeepSeek base URL must be HTTPS without credentials/query/fragment")
        return cls(root, os.getenv("DEEPSEEK_API_KEY", ""), base, os.getenv("DEEPSEEK_MODEL", ""))
