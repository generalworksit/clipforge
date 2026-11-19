"""Local-only monitor routes, protected by the application's origin/host guard."""
import base64
import shutil
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from ..captions import PRESETS
from . import credentials, store, youtube
from .connectors import validate_feed
from .service import supervisor

router = APIRouter(prefix="/api/monitor")


class KeyInput(BaseModel):
    key: str = Field(min_length=15, max_length=256)


class MonitorInput(BaseModel):
    url: str = Field(max_length=2048)
    feed: str = Field(default="", max_length=4096)
    enabled: bool = False
    chat: bool = True
    audio: bool = False
    pre: int = Field(default=20, ge=5, le=40)
    tail: int = Field(default=12, ge=5, le=25)
    max: int = Field(default=60, ge=45, le=120)
    buffer: int = Field(default=150, ge=120, le=180)
    offset: int = Field(default=0, ge=0, le=30)
    sensitivity: str = "normal"
    aspect: str = "landscape"
    preset: str = "none"
    translate: bool = False
    clear_feed: bool = False


def find(monitor_id):
    c = next((c for c in store.all_monitors() if c["id"] == monitor_id), None)
    if not c:
        raise HTTPException(404, "Monitor not found.")
    return c


@router.get("")
def overview():
    monitors = []
    for c in store.all_monitors():
        c["has_feed"] = bool(c.pop("feed", "")); monitors.append(c)
    store.DATA.mkdir(parents=True, exist_ok=True)
    return {"monitors": monitors, "key_configured": credentials.configured(), "quota_used": store.quota_used(),
            "quota_budget": 8500, "max_active": 1, "free_bytes": shutil.disk_usage(store.DATA).free,
            "engine_running": bool(supervisor.thread and supervisor.thread.is_alive()),
            "presets": [{"id": k, "label": v["label"]} for k, v in PRESETS.items()]}


@router.post("/key")
def save_key(body: KeyInput):
    try:
        credentials.save_key(body.key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"configured": True}


@router.post("/key/check")
def check_key():
    monitors = store.all_monitors()
    if not monitors:
        raise HTTPException(400, "Save a YouTube livestream URL first, then test the connection.")
    try:
        result = youtube.status(monitors[0]["url"], credentials.get_key())
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from None
    except Exception:
        raise HTTPException(503, "YouTube could not be reached. Check your internet connection.") from None
    return {"state": result["state"], "chat_available": bool(result.get("chat_id"))}


def validate(body):
    c = body.model_dump()
    c.pop("clear_feed")
    try:
        youtube.video_id(c["url"])
        if c["feed"]:
            c["feed"] = base64.b64encode(credentials._crypt(validate_feed(c["feed"]).encode())).decode()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if c["sensitivity"] not in {"low", "normal", "high"} or c["aspect"] not in {"landscape", "vertical"} or c["preset"] not in PRESETS:
        raise HTTPException(400, "Choose a valid sensitivity, format, and caption style.")
    if c["buffer"] < c["max"] + c["offset"]:
        raise HTTPException(400, "Buffer must cover maximum clip length plus timing offset.")
    c["platform"] = "youtube"
    return c


@router.post("")
def add(body: MonitorInput):
    if len(store.all_monitors()) >= 20:
        raise HTTPException(400, "This prototype supports up to 20 saved stream URLs.")
    c = validate(body)
    with store._lock:
        if c["enabled"] and any(m["enabled"] for m in store.all_monitors()):
            raise HTTPException(409, "Pause the active monitor first. This MVP records one stream at a time.")
        return {"id": store.save(c)}


@router.put("/{monitor_id}")
def update(monitor_id: str, body: MonitorInput):
    with store._lock:
        old = find(monitor_id)
        if old["enabled"] or body.enabled:
            raise HTTPException(409, "Pause this monitor before changing its settings.")
        c = validate(body)
        if not body.feed and not body.clear_feed:
            c["feed"] = old.get("feed", "")
        store.save(c, monitor_id)
    return {"id": monitor_id}


@router.post("/{monitor_id}/toggle")
def toggle(monitor_id: str):
    with store._lock:
        c = find(monitor_id); c.pop("runtime")
        if not c["enabled"] and any(m["enabled"] for m in store.all_monitors()):
            raise HTTPException(409, "Pause the active monitor first. This MVP records one stream at a time.")
        c["enabled"] = not c["enabled"]
        c.pop("id"); store.save(c, monitor_id)
        return {"enabled": c["enabled"]}


@router.post("/{monitor_id}/mark")
def mark(monitor_id: str):
    find(monitor_id)
    try:
        supervisor.mark(monitor_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return {"queued": True}


@router.delete("/{monitor_id}")
def delete(monitor_id: str):
    c = find(monitor_id)
    if c["enabled"]:
        raise HTTPException(409, "Pause monitoring before removing this stream.")
    store.remove(monitor_id)
    return {"deleted": True}
