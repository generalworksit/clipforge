"""Local publishing queue. Drafts are snapshots; approvals never apply to future clips."""
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager, closing
from ..config import ROOT

DATA = ROOT / "publishing-data"
lock = threading.RLock()


@contextmanager
def db():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock, closing(sqlite3.connect(DATA/"queue.sqlite3",timeout=10)) as c, c:
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE IF NOT EXISTS items (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        if os.name != "nt": os.chmod(DATA/"queue.sqlite3",0o600)
        yield c


def all_items():
    with db() as c: return [json.loads(r[0]) for r in c.execute("SELECT data FROM items ORDER BY rowid DESC")]


def get(item_id):
    with db() as c:
        row=c.execute("SELECT data FROM items WHERE id=?",(item_id,)).fetchone()
        if not row: raise ValueError("Publishing item not found.")
        return json.loads(row[0])


def update(item_id, **changes):
    with lock:
        item=get(item_id);item.update(changes)
        with db() as c:c.execute("UPDATE items SET data=? WHERE id=?",(json.dumps(item),item_id))
        return item


def public(item):
    return {**{k:v for k,v in item.items() if k not in {"session","sha256"}},"has_session":bool(item.get("session"))}


def media(item_id):
    if len(item_id)!=32 or any(c not in "0123456789abcdef" for c in item_id): raise ValueError("Invalid publishing item.")
    return DATA/"media"/(item_id+".mp4")


def create(metadata, source):
    item_id=uuid.uuid4().hex
    target=media(item_id);target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if shutil.disk_usage(DATA).free < source.stat().st_size+512*1024**2: raise ValueError("Not enough disk space to keep a publishing copy.")
    sha=hashlib.sha256()
    try:
        with source.open("rb") as src,target.open("xb") as dst:
            for block in iter(lambda:src.read(1024**2),b""):sha.update(block);dst.write(block)
        item={**metadata,"id":item_id,"state":"draft","created":time.time(),"sha256":sha.hexdigest(),
              "size":target.stat().st_size,"attempts":0,"next_try":0,"error":"","video_id":"","session":""}
        with lock:
            if any(x["sha256"]==item["sha256"] and x["state"]!="cancelled" for x in all_items()):
                raise ValueError("This video is already in the publishing queue.")
            with db() as c:c.execute("INSERT INTO items VALUES (?,?)",(item_id,json.dumps(item)))
        return item
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def settings():
    with db() as c:
        row=c.execute("SELECT value FROM settings WHERE id=1").fetchone()
        return {"daily_limit":4,**(json.loads(row[0]) if row else {})}


def save_settings(value):
    with db() as c:c.execute("INSERT INTO settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",(json.dumps(value),))


def claim():
    with lock:
        items=all_items();now=time.time()
        if sum(x.get("uploaded_at",0)>now-86400 for x in items)>=settings()["daily_limit"]:return None
        for item in reversed(items):
            if item["state"] in {"approved","retry"} and item.get("next_try",0)<=now:
                return update(item["id"],state="uploading",attempts=item["attempts"]+1,error="")
    return None
