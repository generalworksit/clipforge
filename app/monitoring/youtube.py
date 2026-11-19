"""YouTube status + official gRPC live chat. No publishing or private-data scopes."""
import hashlib
import re
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
import requests
from . import store


def video_id(url):
    p = urlparse(url)
    if p.scheme != "https" or p.username or p.port:
        raise ValueError("Use an HTTPS YouTube livestream URL.")
    if p.hostname == "youtu.be":
        value = p.path.strip("/")
    elif p.hostname in {"www.youtube.com", "youtube.com", "m.youtube.com"}:
        value = parse_qs(p.query).get("v", [""])[0] if p.path == "/watch" else p.path.split("/")[-1] if p.path.startswith(("/live/", "/shorts/")) else ""
    else:
        value = ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        raise ValueError("This first monitor accepts a specific YouTube livestream URL. Channel discovery comes next.")
    return value


def status(url, key):
    store.spend(1)
    response = requests.get("https://www.googleapis.com/youtube/v3/videos", params={"id": video_id(url), "part": "snippet,liveStreamingDetails"},
                            headers={"X-Goog-Api-Key": key}, timeout=15)
    if response.status_code != 200:
        try:
            error = response.json()["error"]
            reason = error.get("errors", [{}])[0].get("reason", "requestRejected")
            message = error.get("message", "").lower()
        except (ValueError, KeyError, IndexError):
            reason = "requestRejected"; message = ""
        if "api key not valid" in message or "api_key_invalid" in message:
            raise RuntimeError("YouTube rejected this API key. Enter a Google Cloud API key for YouTube Data API v3, not the Chrome companion pairing key.")
        if reason in {"accessNotConfigured", "serviceDisabled"}:
            raise RuntimeError("Enable YouTube Data API v3 in the Google Cloud project that owns this key.")
        if reason in {"quotaExceeded", "dailyLimitExceeded"}:
            raise RuntimeError("YouTube API quota is exhausted. Check your Google Cloud quota before retrying.")
        raise RuntimeError(f"YouTube status HTTP {response.status_code}: {reason}. Check API access and quota.")
    items = response.json().get("items", [])
    if not items:
        raise RuntimeError("YouTube video was not found or is not accessible with this key.")
    item = items[0]; snippet = item.get("snippet", {}); live = item.get("liveStreamingDetails", {})
    return {"state": "live" if snippet.get("liveBroadcastContent") == "live" and not live.get("actualEndTime") else "upcoming" if snippet.get("liveBroadcastContent") == "upcoming" else "offline",
            "title": snippet.get("title", "YouTube livestream"), "creator": snippet.get("channelTitle", ""),
            "chat_id": live.get("activeLiveChatId"), "started": live.get("actualStartTime"), "id": item["id"]}


def message_types():
    """Minimal wire-compatible descriptors using Google's documented field numbers.

    Only fields consumed here are declared; protobuf safely preserves/ignores unknowns.
    Reference: developers.google.com/youtube/data/live/streaming-live-chat
    """
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
    file = descriptor_pb2.FileDescriptorProto(name="clipforge_youtube.proto", package="clipforge.yt", syntax="proto2")
    def define(name, fields):
        message = file.message_type.add(name=name)
        for label, kind, field, number in fields:
            f = message.field.add(name=field, number=number, label=label, type=9 if kind == "str" else 5 if kind == "int" else 11)
            if kind not in ("str", "int"):
                f.type_name = ".clipforge.yt." + kind
    define("Request", [(1,"str","live_chat_id",1),(1,"str","page_token",99),(3,"str","part",100)])
    define("Snippet", [(1,"int","type",1),(1,"str","author_channel_id",301),(1,"str","published_at",4),(1,"str","display_message",16)])
    define("Message", [(1,"str","id",101),(1,"Snippet","snippet",2)])
    define("Response", [(1,"str","offline_at",2),(1,"str","next_page_token",100602),(3,"Message","items",1007)])
    pool = descriptor_pool.DescriptorPool(); pool.Add(file)
    return tuple(message_factory.GetMessageClass(pool.FindMessageTypeByName("clipforge.yt." + name)) for name in ("Request", "Response"))


class Chat:
    def __init__(self, chat_id, key, receive, report):
        self.chat_id, self.key, self.receive, self.report = chat_id, key, receive, report
        self.stopped = threading.Event(); self.channel = None
        self.thread = threading.Thread(target=self.run, daemon=True, name="youtube-chat")

    def start(self):
        self.thread.start()

    def close(self):
        self.stopped.set()
        if self.channel:
            self.channel.close()
        self.thread.join(timeout=5)

    def run(self):
        try:
            import grpc
            Request, Response = message_types()
        except ImportError:
            self.report("Chat needs the dependencies in requirements-live.txt."); return
        token = ""; seen = {}; connected_at = datetime.now(timezone.utc); delay = 2
        while not self.stopped.is_set():
            try:
                store.spend(5)
                self.channel = grpc.secure_channel("youtube.googleapis.com:443", grpc.ssl_channel_credentials())
                call = self.channel.unary_stream("/youtube.api.v3.V3DataLiveChatMessageService/StreamList",
                    request_serializer=lambda m: m.SerializeToString(), response_deserializer=Response.FromString)
                self.report("Connecting to official YouTube chat")
                for reply in call(Request(live_chat_id=self.chat_id, page_token=token, part=["id", "snippet"]), metadata=(("x-goog-api-key", self.key),)):
                    if self.stopped.is_set():
                        break
                    token = reply.next_page_token or token; delay = 2
                    self.report("Official YouTube chat connected")
                    for item in reply.items:
                        s = item.snippet
                        if item.id in seen or s.type not in (1, 15, 17) or not s.display_message:
                            continue
                        seen[item.id] = True
                        if len(seen) > 10000:
                            del seen[next(iter(seen))]
                        try:
                            when = datetime.fromisoformat(s.published_at.replace("Z", "+00:00"))
                            if when < connected_at:  # initial history is not a new live reaction
                                continue
                        except (ValueError, TypeError):
                            continue
                        self.receive({"author": hashlib.sha256(s.author_channel_id.encode()).hexdigest()[:16], "text": s.display_message[:500], "published": when.timestamp()})
                    if reply.offline_at:
                        self.report("YouTube chat ended"); return
                if self.stopped.is_set():
                    return
            except grpc.RpcError as exc:
                if self.stopped.is_set():
                    return
                self.report("YouTube chat: " + exc.code().name)
                if exc.code() in (grpc.StatusCode.PERMISSION_DENIED, grpc.StatusCode.UNAUTHENTICATED, grpc.StatusCode.NOT_FOUND, grpc.StatusCode.FAILED_PRECONDITION):
                    return
            except Exception as exc:
                self.report("Chat unavailable: " + str(exc)[:180])
            finally:
                if self.channel:
                    self.channel.close()
            self.stopped.wait(delay); delay = min(delay * 2, 120)
