"""Notifications: how the swarm gets your attention without needing you.

Everything is best-effort. A notifier that cannot reach Telegram must never
take the swarm down with it, so failures are logged and swallowed.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from .config import NotifyConfig
from .log import get_logger

log = get_logger("notify")


class Notifier:
    def __init__(self, cfg: NotifyConfig, workdir: Path):
        self.cfg = cfg
        self.workdir = workdir
        self.workdir.mkdir(parents=True, exist_ok=True)

    def send(self, title: str, body: str = "") -> None:
        self._to_file(title, body)
        if self.cfg.desktop:
            self._to_desktop(title, body)
        if self.cfg.telegram.enabled:
            self._to_telegram(title, body)

    # ------------------------------------------------------------------
    def _to_file(self, title: str, body: str) -> None:
        import time

        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {title}"
        if body:
            line += f"\n    {body.strip()}"
        try:
            with (self.workdir / "notifications.log").open("a") as fh:
                fh.write(line + "\n")
        except OSError as exc:  # pragma: no cover - disk failure
            log.warning("could not write notification log: %s", exc)

    def _to_desktop(self, title: str, body: str) -> None:
        system = platform.system()
        try:
            if system == "Darwin":
                script = f'display notification {_osa(body)} with title {_osa(title)}'
                subprocess.run(["osascript", "-e", script], check=False, timeout=10)
            elif system == "Linux" and shutil.which("notify-send"):
                subprocess.run(["notify-send", title, body], check=False, timeout=10)
            elif system == "Windows":  # pragma: no cover - platform specific
                ps = (
                    "[reflection.assembly]::loadwithpartialname('System.Windows.Forms');"
                    "$n=New-Object System.Windows.Forms.NotifyIcon;"
                    "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
                    f"$n.ShowBalloonTip(10000,'{title}','{body}',"
                    "[System.Windows.Forms.ToolTipIcon]::None)"
                )
                subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False, timeout=15)
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("desktop notification failed: %s", exc)

    def _to_telegram(self, title: str, body: str) -> None:
        token, chat_id = self.cfg.telegram.token, self.cfg.telegram.chat_id
        if not token or not chat_id:
            log.warning(
                "telegram enabled but %s / %s are not set in the environment",
                self.cfg.telegram.token_env,
                self.cfg.telegram.chat_id_env,
            )
            return
        try:
            import requests

            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": f"*{title}*\n{body}", "parse_mode": "Markdown"},
                timeout=15,
            )
        except Exception as exc:  # noqa: BLE001 - never let notification kill a tick
            log.warning("telegram notification failed: %s", exc)


def _osa(text: str) -> str:
    """Quote a string for AppleScript."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
