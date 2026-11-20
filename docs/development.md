# Development

Python/FastAPI backend, plain JavaScript dashboard, Manifest V3 Chrome companion. FFmpeg renders media; faster-whisper transcribes speech. Heavy jobs run serially; downloads use a separate worker.

| Folder | Purpose |
|---|---|
| `app/` | API, job queue, video and subtitle tools |
| `app/monitoring/` | Experimental YouTube connector, rolling buffer and monitoring |
| `app/publishing/` | Reviewed snapshots, Desktop OAuth, resumable YouTube uploads and persistent queue |
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
.venv\Scripts\python tests/browser_gaming.py
.venv\Scripts\python tests/browser_caption_timing.py
.venv\Scripts\python tests/browser_publishing.py
```

Browser checks use installed Google Chrome. `tests/browser_live.py` exercises real MediaRecorder/FFmpeg using generated input. `tools/smoke_monitor.py` additionally needs cached Kokoro/Whisper models and tests real transcription on generated speech. Synthetic chat and mocked browser APIs do not validate a real platform stream.

The app binds to loopback. The companion stores a local connection key; YouTube API credentials use Windows DPAPI. Automatic pairing runs only after a popup action, through the local app page. Foreign websites cannot send recorder controls. Do not commit runtime folders, keys, cookies or user media.

The background monitor defaults to two recordings (configurable 1–4), a shared 2 GiB buffer budget and 8 GiB free-space reserve. Offline watchers do not consume recording slots. Exports remain serialized; extra live streams wait. Resource changes apply to new recordings. Windows sleep/forced-restart behavior and multi-hour real-stream operation still need validation.

`test_multistream.py` runs two actual FFmpeg buffers on generated footage and verifies separate exports; its provider/supervisor checks use fixtures. `test_caption_timing.py` reproduces delayed audio extraction, pause boundaries and correction/rebuild requests. The WAV fix preserves initial silence; it does not establish recognition accuracy for arbitrary speech. Existing captions need Re-transcribe to rebuild affected timings.

Publishing tests mock Google responses for OAuth, explicit approvals, duplicate checks, daily limits and recovery after a lost final acknowledgement. The browser suite uses real local drafts/previews but a fixture account, with no publisher thread. Real Google authorization and upload require user configuration and have not been claimed tested.

Bundled fonts retain their licenses beside the font files. Model weights are downloaded separately and are not included in the repository.

[Reference project review](reference-review.md) covers the six clipping projects inspected for the gaming editor. `tools/smoke_gaming.py <permitted-local-speech.mp4>` verifies an actual Whisper + FFmpeg export using the cached small model; it writes only under `artifacts/`.
