"""Independent stream watchers with bounded recording slots and durable settings."""
import base64
import queue
import re
import shutil
import threading
import time
import uuid
from . import credentials, store
from .buffer import RollingBuffer, audio_levels
from .connectors import CONNECTORS
from .detector import Detector
from .processing import enqueue


class CaptureSlots:
    def __init__(self):
        self.active = set()
        self.lock = threading.Lock()

    def acquire(self, monitor_id):
        with self.lock:
            if len(self.active) >= store.settings()["max_active"]:
                return False
            self.active.add(monitor_id)
            return True

    def release(self, monitor_id):
        with self.lock:
            self.active.discard(monitor_id)


class Watch(threading.Thread):
    def __init__(self, config, slots=None):
        super().__init__(daemon=True, name="clipforge-monitor")
        self.config = config
        self.stopped = threading.Event()
        self.manual = threading.Event()
        self.messages = queue.Queue(maxsize=5000)
        self.chat_state = "Not connected"
        self.last_job = None
        self.warning = ""
        self.started_at = time.time()
        self.buffer = None
        self.slots = slots or CaptureSlots()

    def report(self, state, detail, **extra):
        store.runtime(self.config["id"], state=state, detail=detail, chat=self.chat_state,
                      last_job=self.last_job, warning=self.warning, session_started=self.started_at, **extra)

    def receive(self, message):
        message["received"] = time.time()
        try:
            self.messages.put_nowait(message)
        except queue.Full:
            self.warning = "Chat queue overflow: some messages were dropped."

    def chat_report(self, detail):
        self.chat_state = detail

    def save(self, event, info):
        if not event or event["end"] - event["start"] < 1:
            return
        try:
            event["timing"] = "arrival-aligned; manual offset applied"
            self.last_job = enqueue(self.buffer, event, self.config, info)
            self.warning = ""
        except (RuntimeError, OSError) as exc:
            self.warning = str(exc)[:250]

    def record(self, connector, info, key):
        self.report("connecting", "Opening the video feed", title=info["title"])
        encrypted = self.config.get("feed")
        resources = store.settings()
        source = credentials._crypt(base64.b64decode(encrypted), True).decode() if encrypted else connector.resolve(self.config["url"], resources["quality"])
        if self.stopped.is_set():
            return
        self.buffer = RollingBuffer(store.DATA / "buffers" / uuid.uuid4().hex, seconds=self.config["buffer"],
            max_bytes=resources["buffer_mb"] * 1024**2 // resources["max_active"], min_free=resources["reserve_gb"] * 1024**3)
        detector = None; chat = None; at = 0; status = {}; latest = 0; last_progress = time.monotonic()
        last_check = time.monotonic(); audio_seen = set(); levels = []; history = []; pending = []
        self.started_at = time.time()
        while not self.messages.empty():
            self.messages.get_nowait()
        try:
            self.buffer.start(source)
            detector = Detector({**self.config, "sound": self.config["audio"]})
            if self.config["chat"] and info.get("chat_id"):
                chat = connector.chat(info["chat_id"], key, self.receive, self.chat_report); chat.start()
            else:
                self.chat_state = "Disabled" if not self.config["chat"] else "Chat unavailable · audio/manual detection only"
            while not self.stopped.wait(0.5):
                segments = self.buffer.refresh()
                if segments and segments[-1].end > latest:
                    latest = segments[-1].end; last_progress = time.monotonic()
                if time.monotonic() - last_progress > 60:
                    raise RuntimeError("Video feed stalled for 60 seconds. Reconnecting with a fresh buffer.")
                if self.config["audio"]:
                    for segment in segments:
                        if segment.path.name not in audio_seen:
                            levels += audio_levels(segment); audio_seen.add(segment.path.name)
                    audio_seen.intersection_update(s.path.name for s in segments)
                while not self.messages.empty():
                    m = self.messages.get_nowait()
                    if 0 <= time.time() - m["published"] <= 30:
                        # Preserve delivery spacing across a completed segment batch.
                        m["media_at"] = max(at + 0.01, latest - (time.time() - m["received"]))
                        pending.append(m)
                pending = pending[-5000:]
                # Tick in media seconds, including low-activity seconds, rather than
                # counting network batches as independent chat spikes.
                while at + 1 <= latest and not self.stopped.is_set():
                    at += 1
                    messages = [m for m in pending if m["media_at"] <= at]
                    pending = [m for m in pending if m["media_at"] > at]
                    rms = next((v for t, v in levels if t <= at < t + 1), 0)
                    # Positive offset moves the candidate window earlier in the video.
                    status = detector.call(type="mark" if self.manual.is_set() else "tick", at=at, messages=messages, rms=rms)
                    self.manual.clear()
                    if status.get("clip"):
                        event = status["clip"]
                        offset = self.config["offset"]
                        event.update(start=max(0, event["start"] - offset), end=max(0, event["end"] - offset), trigger=max(0, event["trigger"] - offset))
                        self.save(event, info)
                    history.append(round(status.get("rate", 0), 2)); history = history[-60:]
                levels = [(t, v) for t, v in levels if t >= at - 2]
                available = latest - segments[0].start if segments else 0
                self.report("recording" if segments else "buffering", "Collecting the reaction" if status.get("active") else "Watching for a highlight",
                    title=info["title"], creator=info.get("creator"), media_seconds=round(latest, 1),
                    buffered_seconds=round(available, 1), detector=status, history=history)
                if self.buffer.process.poll() is not None:
                    raise RuntimeError("Video feed ended or disconnected. Checking the stream again shortly.")
                if time.monotonic() - last_check >= 90:
                    last_check = time.monotonic()
                    check = connector.status(self.config["url"], key)
                    if check["state"] != "live":
                        break
        finally:
            try:
                if chat:
                    chat.close()
            finally:
                try:
                    if detector:
                        try:
                            event = detector.call(type="flush", at=at).get("clip")
                            if event:
                                offset = self.config["offset"]
                                event.update(start=max(0, event["start"]-offset), end=max(0, event["end"]-offset), trigger=max(0, event["trigger"]-offset), interrupted=True)
                                self.save(event, info)
                        finally:
                            detector.close()
                finally:
                    self.buffer.discard(); self.buffer = None

    def run(self):
        connector = CONNECTORS[self.config["platform"]]
        delay = 15
        while not self.stopped.is_set():
            try:
                if not credentials.configured():
                    self.report("needs_setup", "Add a YouTube Data API key to start monitoring.")
                    self.stopped.wait(10); continue
                if shutil.disk_usage(store.DATA).free < store.settings()["reserve_gb"] * 1024**3:
                    self.report("storage_full", "Free disk space is below your recording reserve. Checking again in 30 seconds.")
                    self.stopped.wait(30); continue
                self.report("checking", "Checking YouTube live status")
                key = credentials.get_key()
                info = connector.status(self.config["url"], key)
                if info["state"] == "live":
                    if not self.slots.acquire(self.config["id"]):
                        self.report("waiting", "Live; waiting for a recording slot", title=info["title"])
                        self.stopped.wait(30)
                        continue
                    try:
                        self.record(connector, info, key)
                    finally:
                        self.slots.release(self.config["id"])
                    delay = 15
                else:
                    self.report(info["state"], "Next status check in 90 seconds", title=info["title"])
                    self.stopped.wait(90)
            except Exception as exc:
                # Requests exceptions may contain URLs; never show feed/key material.
                detail = str(exc)[:250] if isinstance(exc, (RuntimeError, ValueError)) else f"{type(exc).__name__}: connection or recording failed."
                self.report("retrying", detail, retry_seconds=delay)
                self.stopped.wait(delay); delay = min(delay * 2, 180)
        self.report("disabled", "Monitoring stopped")


class Supervisor:
    def __init__(self):
        self.stopped = threading.Event(); self.thread = None
        self.watches = {}; self.slots = CaptureSlots(); self.lock = threading.RLock()

    @property
    def watch(self):
        return next(iter(self.watches.values()), None)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stopped.clear()
        # The main jobs lock is held before this starts. Abandoned buffer sessions
        # contain only disposable footage; queued captures live in job folders.
        root = store.DATA / "buffers"
        if root.exists():
            for p in root.iterdir():
                if p.is_dir() and not p.is_symlink() and re.fullmatch(r"[a-f0-9]{32}", p.name):
                    RollingBuffer(p).discard()
        for c in store.all_monitors():
            store.runtime(c["id"], state="starting" if c["enabled"] else "disabled", detail="App restarted" if c["enabled"] else "Not started")
        self.thread = threading.Thread(target=self.run, daemon=True, name="monitor-supervisor"); self.thread.start()

    def run(self):
        while not self.stopped.wait(1):
            configs = {c["id"]: {k: v for k, v in c.items() if k != "runtime"} for c in store.all_monitors() if c["enabled"]}
            with self.lock:
                for mid, watch in list(self.watches.items()):
                    if watch.config != configs.get(mid):
                        watch.stopped.set()
                    if not watch.is_alive():
                        del self.watches[mid]
                for mid, config in configs.items():
                    if mid not in self.watches:
                        watch = Watch(config, self.slots); self.watches[mid] = watch; watch.start()
        with self.lock:
            for watch in self.watches.values():
                watch.stopped.set()

    def close(self):
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=3)
        with self.lock:
            watches = list(self.watches.values())
        for watch in watches:
            watch.stopped.set()
            if watch.buffer:
                watch.buffer.close()
        deadline = time.monotonic() + 50
        for watch in watches:
            watch.join(timeout=max(0, deadline-time.monotonic()))

    def mark(self, monitor_id):
        with self.lock:
            watch = self.watches.get(monitor_id)
            if not watch or not watch.buffer or not watch.buffer.segments:
                raise ValueError("Wait until this monitor has buffered some video.")
            watch.manual.set()


supervisor = Supervisor()
