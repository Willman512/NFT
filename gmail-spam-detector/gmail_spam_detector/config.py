import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class ConfigError(Exception):
    pass


@dataclass
class Config:
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )
    credentials_file: Path = field(
        default_factory=lambda: Path(
            os.getenv("CREDENTIALS_FILE", "config/credentials.json")
        )
    )
    token_file: Path = field(
        default_factory=lambda: Path(
            os.getenv("TOKEN_FILE", "data/token.json")
        )
    )
    batch_size: int = field(
        default_factory=lambda: int(os.getenv("BATCH_SIZE", "25"))
    )
    claude_model: str = field(
        default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
    )
    max_emails: int = field(
        default_factory=lambda: int(os.getenv("MAX_EMAILS", "200"))
    )

    def validate(self) -> None:
        if not self.anthropic_api_key:
            raise ConfigError(
                "ANTHROPIC_API_KEY is not set. "
                "Set it in your environment or in a .env file."
            )
        if not self.credentials_file.exists():
            raise ConfigError(
                f"Gmail credentials file not found: {self.credentials_file}\n"
                "Download it from Google Cloud Console and save it there."
            )
