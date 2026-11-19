"""Provider boundary: status, chat and media resolution are separate capabilities."""
import subprocess
import sys
from urllib.parse import urlparse
from . import youtube
from .buffer import HIDDEN


def validate_feed(url):
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or len(url) > 4096:
        raise ValueError("Use an HTTP(S) media feed URL without embedded username/password.")
    return url


class YouTube:
    platform = "youtube"
    status = staticmethod(youtube.status)
    chat = youtube.Chat

    @staticmethod
    def resolve(url):
        try:
            result = subprocess.run([sys.executable, "-m", "streamlink", "--stream-url", url, "720p,720p60,best"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40, creationflags=HIDDEN)
        except subprocess.TimeoutExpired:
            raise RuntimeError("Media lookup timed out. Check the stream or use your authorized media feed.") from None
        media = result.stdout.strip()
        if result.returncode or not media.startswith(("https://", "http://")) or "\n" in media:
            raise RuntimeError("Streamlink could not open this stream. It may be offline, restricted, or unsupported. Use an authorized media feed or the Chrome companion.")
        return validate_feed(media)


CONNECTORS = {"youtube": YouTube()}
