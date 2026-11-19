"""Python transport for the same detector used by ClipForge Live in Chrome."""
import json
import queue
import shutil
import subprocess
import threading
from ..config import ROOT
from .buffer import HIDDEN


class Detector:
    def __init__(self, options):
        if not shutil.which("node"):
            raise RuntimeError("Install Node.js 22 or newer, then restart ClipForge to enable detection.")
        self.process = subprocess.Popen(["node", str(ROOT / "tools" / "detector_bridge.mjs")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1, creationflags=HIDDEN)
        self.replies = queue.Queue()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.call(type="init", options=options)
        except Exception:
            self.close()
            raise

    def _read(self):
        for line in self.process.stdout:
            self.replies.put(line)
        self.replies.put(None)

    def call(self, **message):
        try:
            self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.process.stdin.flush()
            line = self.replies.get(timeout=5)
            if not line:
                raise RuntimeError("Highlight detector stopped unexpectedly.")
            result = json.loads(line)
            if result.get("error"):
                raise RuntimeError(result["error"])
            return result
        except (BrokenPipeError, queue.Empty):
            raise RuntimeError("Highlight detector is not responding.") from None

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        self.process.stdin.close(); self.process.stdout.close()
