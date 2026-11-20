"""Desktop OAuth with PKCE, encrypted tokens and resumable official YouTube uploads."""
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse
import requests
from ..monitoring.credentials import _crypt
from . import store

SCOPE="https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
TOKEN="https://oauth2.googleapis.com/token"
API="https://www.googleapis.com/youtube/v3"
_pending={}
_lock=threading.RLock()


class ReviewRequired(RuntimeError): pass
class TryLater(RuntimeError): pass


def seal(value):return base64.b64encode(_crypt(json.dumps(value).encode())).decode()
def unseal(value):return json.loads(_crypt(base64.b64decode(value),True))


def read_secret(name):
    path=store.DATA/(name+".dpapi")
    return unseal(path.read_text()) if path.exists() else None


def write_secret(name, value):
    store.DATA.mkdir(exist_ok=True,parents=True,mode=0o700)
    path=store.DATA/(name+".dpapi");tmp=path.with_suffix(".tmp")
    with _lock:
        tmp.write_text(seal(value),encoding="ascii")
        if os.name!="nt":os.chmod(tmp,0o600)
        tmp.replace(path)


def configure(data):
    c=data.get("installed",{}) if isinstance(data,dict) else {}
    if not isinstance(c,dict) or not str(c.get("client_id","")).endswith(".apps.googleusercontent.com") or not c.get("client_secret"):
        raise ValueError("Use the Google OAuth JSON for a Desktop app, not an API key or Web client.")
    if read_secret("account"):raise ValueError("Disconnect the account before replacing its OAuth app.")
    write_secret("client",{k:c[k] for k in ("client_id","client_secret")})


def account():
    a=read_secret("account")
    return {"configured":bool(read_secret("client")),"connected":bool(a),
            "channel_id":a.get("channel_id") if a else None,"title":a.get("title") if a else None}


def begin(redirect):
    c=read_secret("client")
    if not c:raise ValueError("Add your Google Desktop OAuth client JSON first.")
    verifier=secrets.token_urlsafe(48);state=secrets.token_urlsafe(32)
    with _lock:
        _pending.clear();_pending[state]={"verifier":verifier,"redirect":redirect,"created":time.time()}
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return "https://accounts.google.com/o/oauth2/v2/auth?"+urlencode({"client_id":c["client_id"],"redirect_uri":redirect,
        "response_type":"code","scope":SCOPE,"state":state,"code_challenge":challenge,"code_challenge_method":"S256",
        "access_type":"offline","prompt":"consent"})


def _check(response):
    if response.status_code not in (200,201):
        if response.status_code in (429,500,502,503,504):raise TryLater("YouTube is temporarily unavailable or rate limited. Retrying later.")
        raise ReviewRequired(f"YouTube rejected the request (HTTP {response.status_code}). Check account access and API setup.")


def _json(response):
    _check(response)
    try:return response.json()
    except ValueError:raise ReviewRequired("YouTube returned an unreadable response. Review before retrying.") from None


def finish(state, code):
    with _lock:pending=_pending.pop(state,None)
    if not pending or time.time()-pending["created"]>600:raise ValueError("Connection request expired. Start Connect YouTube again.")
    if not code:raise ValueError("YouTube connection was not approved.")
    c=read_secret("client")
    tokens=_json(requests.post(TOKEN,data={**c,"grant_type":"authorization_code","code":code,
        "redirect_uri":pending["redirect"],"code_verifier":pending["verifier"]},timeout=30))
    if not tokens.get("refresh_token"):raise ValueError("Google did not grant background access. Reconnect and approve the requested permissions.")
    channels=_json(requests.get(API+"/channels",params={"part":"snippet","mine":"true"},
        headers={"Authorization":"Bearer "+tokens["access_token"]},timeout=30)).get("items",[])
    if len(channels)!=1:raise ValueError("Choose one YouTube channel when connecting.")
    write_secret("account",{**tokens,"expires":time.time()+tokens.get("expires_in",3600),
        "channel_id":channels[0]["id"],"title":channels[0]["snippet"]["title"]})
    return account()


def token(channel_id):
    with _lock:
        a=read_secret("account")
        if not a or a["channel_id"]!=channel_id:raise ReviewRequired("Reconnect the YouTube channel that approved this upload.")
        if a.get("expires",0)<time.time()+120:
            c=read_secret("client")
            fresh=_json(requests.post(TOKEN,data={**c,"grant_type":"refresh_token","refresh_token":a["refresh_token"]},timeout=30))
            a.update(fresh);a["expires"]=time.time()+fresh.get("expires_in",3600);write_secret("account",a)
        return a["access_token"]


def disconnect():
    # Local disconnection stops future queue work; OAuth can also be revoked in Google Account settings.
    with _lock:
        _pending.clear();(store.DATA/"account.dpapi").unlink(missing_ok=True)


def metadata(item):
    status={"privacyStatus":item["privacy"],"selfDeclaredMadeForKids":item["made_for_kids"],"containsSyntheticMedia":item["synthetic"]}
    if item.get("publish_at"):
        if item["publish_at"]<time.time()+60:raise ReviewRequired("The scheduled time has passed or is too close. Choose a new time and approve again.")
        status.update(privacyStatus="private",publishAt=datetime.fromtimestamp(item["publish_at"],timezone.utc).isoformat().replace("+00:00","Z"))
    return {"snippet":{"title":item["title"],"description":item["description"],"categoryId":"20"},"status":status}


def session_url(url):
    p=urlparse(url)
    if p.scheme!="https" or p.hostname not in {"www.googleapis.com","youtube.googleapis.com"} or p.username is not None or p.password is not None or p.port not in (None,443):
        raise ReviewRequired("Unexpected upload server. Upload stopped.")
    return url


def upload(item, stopped):
    path=store.media(item["id"])
    if not path.is_file() or path.stat().st_size!=item["size"]:raise ReviewRequired("The reviewed publishing copy is missing or changed.")
    digest=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024**2),b""):digest.update(block)
    if digest.hexdigest()!=item["sha256"]:raise ReviewRequired("The video changed after review. Create and approve a new draft.")
    headers={"Authorization":"Bearer "+token(item["channel_id"])}
    total=item["size"]
    if item.get("session"):
        url=session_url(unseal(item["session"]))
    else:
        r=requests.post("https://www.googleapis.com/upload/youtube/v3/videos",params={"uploadType":"resumable","part":"snippet,status","notifySubscribers":"false"},
            headers={**headers,"X-Upload-Content-Length":str(total),"X-Upload-Content-Type":"video/mp4"},json=metadata(item),timeout=30,allow_redirects=False)
        _check(r);url=session_url(r.headers.get("Location",""))
        store.update(item["id"],session=seal(url))
    # Always query the server before sending bytes, including after a lost final acknowledgement.
    r=requests.put(url,headers={**headers,"Content-Length":"0","Content-Range":f"bytes */{total}"},data=b"",timeout=30,allow_redirects=False)
    offset=0;sent_from=None
    while True:
        if r.status_code in (200,201):
            result=_json(r);vid=result.get("id","")
            if not re.fullmatch(r"[A-Za-z0-9_-]{11}",vid):raise ReviewRequired("Upload may have completed, but no video ID was returned. Check YouTube Studio before retrying.")
            return vid
        if r.status_code in (404,410):raise ReviewRequired("Upload session expired. Check YouTube Studio for this video before creating another draft.")
        if r.status_code!=308:_json(r)
        received=r.headers.get("Range","")
        if received:
            match=re.fullmatch(r"bytes=0-(\d+)",received)
            if not match:raise ReviewRequired("Unexpected upload position. Check YouTube Studio.")
            offset=int(match[1])+1
        if sent_from is not None and offset<=sent_from:
            raise TryLater("YouTube has not acknowledged new bytes. Resuming later from its confirmed position.")
        if offset>=total:raise TryLater("YouTube is confirming the completed upload. Checking again shortly.")
        if stopped.is_set():raise TryLater("Upload paused while ClipForge closes. It will resume on restart.")
        # Eight MiB chunks are multiples of Google's 256 KiB requirement.
        with path.open("rb") as f:f.seek(offset);chunk=f.read(8*1024**2)
        sent_from=offset
        headers={"Authorization":"Bearer "+token(item["channel_id"])}
        r=requests.put(url,headers={**headers,"Content-Length":str(len(chunk)),"Content-Type":"video/mp4",
            "Content-Range":f"bytes {offset}-{offset+len(chunk)-1}/{total}"},data=chunk,timeout=90,allow_redirects=False)
