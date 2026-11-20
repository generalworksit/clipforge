"""Only individually approved snapshots are uploaded. No automatic content selection."""
import threading
import time
import requests
from . import store, youtube


class Publisher:
    def __init__(self):self.stopped=threading.Event();self.thread=None

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stopped.clear()
        for item in store.all_items():
            if item["state"]=="uploading":store.update(item["id"],state="retry",next_try=0,error="Resuming interrupted upload.")
        self.thread=threading.Thread(target=self.run,name="clipforge-publisher",daemon=True);self.thread.start()

    def step(self):
        item=store.claim()
        if not item:return False
        try:
            video_id=youtube.upload(item,self.stopped)
            store.update(item["id"],state="uploaded",video_id=video_id,uploaded_at=time.time(),error="",session="")
        except youtube.ReviewRequired as exc:
            store.update(item["id"],state="needs_review",error=str(exc))
        except (requests.RequestException,youtube.TryLater):
            retry=item["attempts"]<6
            store.update(item["id"],state="retry" if retry else "needs_review",next_try=time.time()+min(3600,30*2**item["attempts"]),
                error="Upload interrupted. The saved session will be checked before resuming." if retry else "Repeated connection failures. Review and resume the saved upload.")
        except Exception:
            store.update(item["id"],state="needs_review",error="Publishing stopped after a local error. Check the connection and reviewed copy before resuming.")
        return True

    def run(self):
        while not self.stopped.wait(3):
            try:self.step()
            except Exception:self.stopped.wait(10)

    def close(self):
        self.stopped.set()
        if self.thread:self.thread.join(timeout=3)


publisher=Publisher()
