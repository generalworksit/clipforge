# ClipForge

A local video clipping workspace for Windows.

- Capture YouTube and Twitch highlights with the Chrome companion.
- Keep the lead-in, detect chat reactions, and save clean MP4 clips.
- Turn recordings into short clips with Whisper subtitles, caption styles, or no captions.
- Preview, trim, reformat, download, and delete clips locally.

## Start

Install Python 3.11+, FFmpeg, and Node.js 22+. Then run:

```bat
run.bat
```

Open **http://127.0.0.1:8000**. Dependencies install on first launch; speech models download when first needed. Ollama is optional for moment selection.

## Live capture

Load the `extension` folder through **chrome://extensions → Developer mode → Load unpacked**. Click **Connect app**, open a livestream with chat, then **Start capture**. Use **Save moment** to mark one yourself.

Clips appear in **Live clips**. The connection is saved automatically; manual pairing remains available.

## Status

The Chrome companion requires the stream tab to stay open and visible. Background **Auto monitor** is an experimental YouTube-only path requiring a valid Google Cloud API key; its local export pipeline is tested, but real broadcast validation is still pending. Twitch/Kick background connectors are not implemented.

English, French, and Russian speech are supported by Whisper; review automatic captions. Use media you own or have permission to process. Nothing is published automatically.

Settings, recordings, credentials, model files, and logs stay outside version control. Optional settings: copy `config.example.yaml` to `config.yaml`.

[Development notes](docs/development.md)
