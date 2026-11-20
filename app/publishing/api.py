import json
import math
import re
import time
from pathlib import Path
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel, Field
from .. import jobs
from ..config import CONFIG
from . import store, youtube

router=APIRouter(prefix="/api/publishing")


class Draft(BaseModel):
    job_id: str
    file: str
    title: str = Field(min_length=1,max_length=100)
    description: str = Field(default="",max_length=5000)
    privacy: str = "private"
    publish_at: float | None = None
    made_for_kids: bool = False
    synthetic: bool = False


class Approval(BaseModel):
    channel_id: str
    consent: bool = False


class Limits(BaseModel):
    daily_limit: int = Field(default=4,ge=1,le=20)


class Cancellation(BaseModel):
    checked_remote: bool = False


def item_or_404(item_id):
    try:return store.get(item_id)
    except ValueError as exc:raise HTTPException(404,str(exc)) from None


def draft_values(body):
    v=body.model_dump()
    if not v["title"].strip() or any(c in v["title"] for c in "<>\x00") or "\x00" in v["description"]:
        raise HTTPException(400,"Enter a valid title and description.")
    if v["privacy"] not in {"private","unlisted","public"}:raise HTTPException(400,"Choose a visibility.")
    at=v["publish_at"]
    if at is not None and (not math.isfinite(at) or at<time.time()+300 or v["privacy"]!="public"):
        raise HTTPException(400,"For a public schedule, choose a time at least five minutes from now.")
    v["title"]=v["title"].strip()
    return v


@router.get("")
def overview():
    return {"account":youtube.account(),"settings":store.settings(),"items":[store.public(i) for i in store.all_items()]}


@router.put("/settings")
def settings(body: Limits):store.save_settings(body.model_dump());return store.settings()


@router.post("/youtube/client")
def configure(file: UploadFile = File(...)):
    raw=file.file.read(16385)
    if len(raw)>16384:raise HTTPException(400,"OAuth client file is too large.")
    try:youtube.configure(json.loads(raw));return youtube.account()
    except (ValueError,TypeError,KeyError):raise HTTPException(400,"Use a valid Google Desktop OAuth client JSON. Disconnect any current account first.") from None


@router.post("/youtube/connect")
def connect():
    try:return {"url":youtube.begin(f"http://127.0.0.1:{CONFIG['server']['port']}/api/publishing/youtube/callback")}
    except ValueError as exc:raise HTTPException(400,str(exc)) from None


@router.get("/youtube/callback",response_class=HTMLResponse)
def callback(state: str = "", code: str = "", error: str = ""):
    try:
        youtube.finish(state,"" if error else code)
        return HTMLResponse("<title>YouTube connected</title><p>YouTube connected. Close this tab and return to ClipForge. No clips have been approved by connecting.</p>",headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer"})
    except Exception:
        return HTMLResponse("<title>Connection not completed</title><p>YouTube connection failed or expired. Return to ClipForge and try Connect again. Check that your Desktop OAuth app has YouTube Data API enabled and your Google account is allowed as a test user.</p>",status_code=400,headers={"Cache-Control":"no-store","Referrer-Policy":"no-referrer"})


@router.post("/youtube/disconnect")
def disconnect():
    with store.lock:
        if any(i["state"]=="uploading" for i in store.all_items()):raise HTTPException(409,"Wait for the active upload to finish before disconnecting.")
        youtube.disconnect()
    return youtube.account()


@router.post("/items",status_code=201)
def create(body: Draft):
    from ..main import _status_or_404, _job_files
    values=draft_values(body);status=_status_or_404(body.job_id)
    if status["state"]!="done" or body.file not in _job_files(status) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.mp4",body.file):
        raise HTTPException(400,"Choose a finished MP4 from the library.")
    source=jobs.job_dir(body.job_id)/body.file
    if not source.is_file() or source.is_symlink():raise HTTPException(400,"Video is not available.")
    try:return store.public(store.create(values,source))
    except ValueError as exc:raise HTTPException(409,str(exc)) from None


@router.get("/items/{item_id}/media")
def preview(item_id: str):
    item_or_404(item_id);path=store.media(item_id)
    if not path.is_file():raise HTTPException(404,"Publishing copy is no longer available.")
    return FileResponse(path,media_type="video/mp4")


@router.put("/items/{item_id}")
def edit(item_id: str, body: Draft):
    with store.lock:
        item=item_or_404(item_id)
        if item["state"] not in {"draft","needs_review"} or item.get("session"):raise HTTPException(409,"Only a draft without an upload session can be edited.")
        if (body.job_id,body.file)!=(item["job_id"],item["file"]):raise HTTPException(400,"Create a new draft to change the source video.")
        return store.public(store.update(item_id,**draft_values(body),state="draft",error=""))


@router.post("/items/{item_id}/approve")
def approve(item_id: str, body: Approval):
    with store.lock:
        item=item_or_404(item_id);account=youtube.account()
        if item["state"] not in {"draft","needs_review"}:raise HTTPException(409,"This item is already approved, uploaded or cancelled.")
        if not body.consent or not account["connected"] or body.channel_id!=account["channel_id"]:
            raise HTTPException(400,"Connect the intended channel, review the clip, then explicitly approve this upload.")
        if item.get("session") and item.get("channel_id")!=body.channel_id:
            raise HTTPException(409,"Reconnect the original channel to resume this upload session.")
        if not item.get("session"):
            draft_values(Draft(**{k:item[k] for k in Draft.model_fields}))
        return store.public(store.update(item_id,state="approved",channel_id=body.channel_id,approved_at=time.time(),next_try=0,attempts=0,error=""))


@router.post("/items/{item_id}/cancel")
def cancel(item_id: str, body: Cancellation | None = None):
    with store.lock:
        item=item_or_404(item_id)
        if item["state"] in {"uploading","uploaded"} or (item.get("session") and not (item["state"]=="needs_review" and body and body.checked_remote)):
            raise HTTPException(409,"This upload has started. Manage or cancel its publication in YouTube Studio.")
        item=store.update(item_id,state="cancelled")
        store.media(item_id).unlink(missing_ok=True)
        return store.public(item)
