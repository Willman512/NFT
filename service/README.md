# Keeping the office open

Forge only earns while it is running. Pick the one for your machine, edit the
paths inside it, and the office will come back after a reboot or a crash.

| File | Machine | Install |
|---|---|---|
| `com.forge.office.plist` | macOS | `cp` into `~/Library/LaunchAgents/`, then `launchctl load -w` it |
| `forge-office.service` | Linux | `cp` into `~/.config/systemd/user/`, then `systemctl --user enable --now forge-office` |
| `forge-office.cmd` | Windows | shortcut it into `shell:startup` |

Each one runs `forge office --no-browser`, which serves the office at
<http://127.0.0.1:8765/> and holds a standup on the interval in your
`forge.yaml`. Open that address whenever you want to look in.

If you would rather have no web UI at all, swap the command for
`forge run` — same standups, logs only.

**A note on sleep.** A laptop that is asleep is not running an office. If you
want it genuinely perpetual, use a machine that stays awake, or accept that it
works the hours you do.
