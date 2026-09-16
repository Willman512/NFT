"""The local web office.

A small Flask app bound to localhost. It owns one Office and serialises
every touch of it behind a lock, because the ledger is a single SQLite
connection and the server answers on worker threads.

The perpetual part lives here too: a background thread holds a standup
whenever one is due, so the office keeps working whether or not the page
is open.
"""

from __future__ import annotations

import math
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from ..core.config import Config
from ..core.log import get_logger
from ..core.money import usd
from ..core.survival import SurvivalOfficer
from ..staff import roles
from ..staff.office import Office

log = get_logger("web")

#: How often the background thread wakes to check whether a standup is due.
HEARTBEAT_SECONDS = 15.0


def json_safe(value: Any) -> Any:
    """Strip out anything ``JSON.parse`` would refuse.

    Python happily serialises ``inf`` and ``nan`` as bare ``Infinity`` and
    ``NaN``; browsers reject the whole document when they see them, which
    fails as a blank page rather than as an error. One stray ratio should
    not be able to take the office down, so everything is swept on the way
    out.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


class OfficeService:
    """One office, safely shared between the scheduler and the web workers."""

    def __init__(self, cfg: Config, office: Office | None = None, autostart: bool = True):
        self.cfg = cfg
        #: Guards the office and its ledger. Held for the length of a standup.
        self.lock = threading.RLock()
        #: Guards only the scheduling flags, so "is one already running?" can
        #: be answered instantly while a standup holds the big lock.
        self._sched_lock = threading.Lock()
        self.office = office or Office(cfg)
        self._thread: threading.Thread | None = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._busy = False
        self._pending = False
        self._closed = False
        self._last_error: str | None = None
        if self.office.ledger.get_state("office:running") is None:
            self.office.ledger.set_state("office:running", "1" if autostart else "0")

    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        with self.lock:
            return self.office.ledger.get_state("office:running", "0") == "1"

    def set_running(self, value: bool) -> None:
        with self.lock:
            self.office.ledger.set_state("office:running", "1" if value else "0")
        log.info("office %s", "started" if value else "paused")

    @property
    def interval_seconds(self) -> float:
        return max(30.0, self.cfg.office.standup_interval_minutes * 60.0)

    def _last_standup(self) -> float:
        with self.lock:
            return float(self.office.ledger.get_state("office:last_standup", "0") or 0)

    def seconds_until_next(self) -> float:
        if not self.running:
            return float("inf")
        return max(0.0, self._last_standup() + self.interval_seconds - time.time())

    # ------------------------------------------------------------------
    def standup(self, *, force: bool = True) -> dict:
        """Hold a standup. Safe to call from any thread."""
        with self.lock:
            if self.office is None:
                return {"error": "the office is closed"}
            self._busy = True
            try:
                report = self.office.standup(force=force)
                self.office.ledger.set_state("office:last_standup", str(time.time()))
                self._last_error = None
                return report.to_dict()
            except Exception as exc:  # noqa: BLE001 - the office must survive a bad cycle
                self._last_error = f"{type(exc).__name__}: {exc}"
                log.exception("standup failed")
                return {"error": self._last_error}
            finally:
                self._busy = False

    def request_standup(self) -> dict:
        """Kick off a standup in the background, if one is not already running.

        Only ever one at a time: a second concurrent standup would compete
        for the same ledger lock and produce two cycle numbers for one pass.
        The handle is kept so shutdown can wait for it — closing the
        database underneath a running standup crashes the interpreter.
        """
        with self._sched_lock:
            if self._closed:
                return {"started": False, "reason": "the office is closed"}
            if self._pending:
                return {"started": False, "reason": "a standup is already running"}
            self._pending = True
            self._worker = threading.Thread(
                target=self._run_standup, name="forge-standup", daemon=True
            )
            self._worker.start()
        return {"started": True}

    def _run_standup(self) -> None:
        try:
            self.standup()
        finally:
            with self._sched_lock:
                self._pending = False

    def close(self, timeout: float = 30.0) -> None:
        """Stop everything, wait for work in flight, then close the books."""
        self.stop_scheduler()
        with self._sched_lock:
            self._closed = True
            threads = [self._worker, self._thread]
        for thread in threads:
            if thread and thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=timeout)
        with self.lock:
            if self.office is not None:
                self.office.close()
                self.office = None  # type: ignore[assignment]

    def __enter__(self) -> "OfficeService":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def start_scheduler(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="forge-scheduler", daemon=True)
        self._thread.start()
        log.info("scheduler started, standup every %.0f minutes",
                 self.cfg.office.standup_interval_minutes)

    def stop_scheduler(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                if self._closed:
                    return
                if self.running and self.seconds_until_next() <= 0:
                    self.standup()
            except Exception:  # noqa: BLE001 - a scheduler that dies is worse than one that errs
                log.exception("scheduler tick failed")

    # ------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        with self.lock:
            ctx = self.office.context()
            feed = [r.to_dict() for r in self.office.memory.feed(limit=80)]
            latest: dict[str, dict] = {}
            for remark in feed:
                latest.setdefault(remark["persona"], remark)
        return {
            **ctx,
            "feed": feed,
            "latest_by_persona": latest,
            "running": self.running,
            "busy": self._busy,
            "seconds_until_next": None if not self.running else round(self.seconds_until_next()),
            "interval_minutes": self.cfg.office.standup_interval_minutes,
            "last_error": self._last_error,
        }


# ----------------------------------------------------------------------
def create_app(cfg: Config, service: OfficeService | None = None) -> Flask:
    app = Flask(
        __name__,
        static_folder=str(Path(__file__).parent / "static"),
        template_folder=str(Path(__file__).parent / "templates"),
    )
    svc = service or OfficeService(cfg)
    app.config["FORGE"] = svc

    @app.get("/")
    def index():  # type: ignore[unused-ignore]
        return render_template("index.html")

    @app.get("/api/state")
    def api_state():
        return jsonify(json_safe(svc.state()))

    @app.get("/api/feed")
    def api_feed():
        since = int(request.args.get("since", 0))
        with svc.lock:
            remarks = [r.to_dict() for r in svc.office.memory.feed(limit=60, since_id=since)]
        return jsonify({"remarks": remarks})

    @app.get("/api/wallet")
    def api_wallet():
        with svc.lock:
            svc.office.wallet.balance(force=True)
            return jsonify(svc.office.wallet.snapshot())

    @app.post("/api/standup")
    def api_standup():
        return jsonify(svc.request_standup())

    @app.post("/api/running")
    def api_running():
        body = request.get_json(silent=True) or {}
        svc.set_running(bool(body.get("running", True)))
        return jsonify({"running": svc.running})

    @app.get("/api/chat/<persona>")
    def api_thread(persona: str):
        if persona not in roles.PERSONAS:
            return jsonify({"error": f"no such member of staff: {persona}"}), 404
        with svc.lock:
            return jsonify({
                "persona": persona,
                "messages": [m.to_dict() for m in svc.office.memory.thread(persona)],
            })

    @app.post("/api/chat/<persona>")
    def api_chat(persona: str):
        if persona not in roles.PERSONAS:
            return jsonify({"error": f"no such member of staff: {persona}"}), 404
        message = (request.get_json(silent=True) or {}).get("message", "").strip()
        if not message:
            return jsonify({"error": "say something"}), 400
        with svc.lock:
            try:
                return jsonify(svc.office.ask(persona, message))
            except Exception as exc:  # noqa: BLE001 - surface it in the UI, do not 500 silently
                log.exception("chat failed")
                return jsonify({"error": f"{type(exc).__name__}: {exc}"}), 500

    @app.post("/api/tasks/<int:task_id>")
    def api_task(task_id: int):
        status = (request.get_json(silent=True) or {}).get("status", "done")
        with svc.lock:
            ok = svc.office.queue.resolve(task_id, status)
        return (jsonify({"ok": True, "status": status}) if ok
                else (jsonify({"error": "no such task"}), 404))

    @app.post("/api/income")
    def api_income():
        body = request.get_json(silent=True) or {}
        venture = body.get("venture", "")
        try:
            amount = float(body.get("amount", 0))
        except (TypeError, ValueError):
            return jsonify({"error": "amount must be a number"}), 400
        if amount <= 0:
            return jsonify({"error": "amount must be positive"}), 400
        with svc.lock:
            if venture not in svc.office.orch.ventures and venture not in svc.office.ledger.ventures():
                return jsonify({"error": f"unknown venture {venture!r}"}), 400
            cents = svc.office.record_income(
                venture, amount, body.get("memo") or "payment received"
            )
        return jsonify({"ok": True, "cents": cents})

    @app.post("/api/venture/<name>/<action>")
    def api_venture(name: str, action: str):
        with svc.lock:
            officer = SurvivalOfficer(svc.office.ledger, svc.office.survival.rules)
            if action == "revive":
                officer.revive(name)
            elif action == "unbench":
                svc.office.orch.unbench(name)
            elif action == "resume":
                svc.office.orch.resume()
            else:
                return jsonify({"error": f"unknown action {action!r}"}), 400
        return jsonify({"ok": True})

    return app


def serve(cfg: Config, *, open_browser: bool = True, service: OfficeService | None = None) -> None:
    svc = service or OfficeService(cfg)
    app = create_app(cfg, svc)
    svc.start_scheduler()
    host, port = cfg.office.host, cfg.office.port
    url = f"http://{host}:{port}/"
    log.info("office open at %s", url)
    if open_browser:
        threading.Timer(1.0, lambda: _try_open(url)).start()
    try:
        app.run(host=host, port=port, threaded=True, use_reloader=False)
    finally:
        # A standup in flight must finish before the database goes away.
        svc.close()


def _try_open(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception as exc:  # noqa: BLE001 - a headless box has no browser
        log.debug("could not open a browser: %s", exc)
