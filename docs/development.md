# Development

Python/FastAPI backend, plain JavaScript dashboard, Manifest V3 Chrome companion. FFmpeg renders media; faster-whisper transcribes speech. Heavy jobs run serially; downloads use a separate worker.

| Folder | Purpose |
|---|---|
| `app/` | API, job queue, video and subtitle tools |
| `app/monitoring/` | Experimental YouTube connector, rolling buffer and monitoring |
| `extension/` | Browser capture and shared reaction detector |
| `static/` | Dashboard and bundled UI fonts |
| `tests/` | API, detector, media and browser checks |

Install development tools in the same virtual environment:

```bat
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m unittest discover -s tests -v
node tests/live_detector.mjs
node tests/live_background.mjs
node tests/live_pairing.mjs
.venv\Scripts\python tests/browser_popup.py
.venv\Scripts\python tests/browser_monitor.py
.venv\Scripts\python tests/browser_delete.py
```

Browser checks use installed Google Chrome. `tests/browser_live.py` exercises real MediaRecorder/FFmpeg using generated input. `tools/smoke_monitor.py` additionally needs cached Kokoro/Whisper models and tests real transcription on generated speech. Synthetic chat and mocked browser APIs do not validate a real platform stream.

The app binds to loopback. The companion stores a local connection key; YouTube API credentials use Windows DPAPI. Automatic pairing runs only after a popup action, through the local app page. Foreign websites cannot send recorder controls. Do not commit runtime folders, keys, cookies or user media.

The background monitor currently permits one stream, has a bounded buffer/queue, and requires 8 GiB free space. Windows sleep/forced-restart behavior and long-running multi-stream operation need further validation before unattended production use.

Bundled fonts retain their licenses beside the font files. Model weights are downloaded separately and are not included in the repository.
