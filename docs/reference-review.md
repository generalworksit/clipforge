# Clipping project review — 19 November 2026

Six source snapshots were downloaded for inspection, without running their installers or importing their code. The checkouts stay under ignored `artifacts/references/`. ClipForge keeps its existing Windows/Python/FFmpeg stack and MIT license; the new implementation is original.

## Findings

| Project / inspected revision | What the source actually adds | Decision for ClipForge |
|---|---|---|
| [gtavi-claudecode](https://github.com/Bodila51/gtavi-claudecode/tree/dd95d393c6f74fe2ac14b0239f8826691e5dcb0e), MIT | Small Python utilities orchestrated by Claude skills. `scripts/facecam.py` samples frames, groups stationary faces and searches for webcam boundaries. `moments.py` combines transcript, audio, replay and chat evidence; `live.py` records segmented footage and aligns reactions. | Useful ergonomic reference for reaction layouts. Its campaign/revenue claims are not detection benchmarks. Keep ClipForge's existing bounded live buffer; add source-region editing and preview. |
| [GTA Clips Kitty plugin](https://github.com/ColinGPT9/Grand-Theft-Auto-6-Clips-Kitty-Plugin/tree/17e710caf8a72b6284e55cec0d42ce83548204fd), MIT | `src/gta_highlights/detector.py` groups transcript events and filters duplicates; `scoring.py` records evidence and rejects negated nominations. Detection depends on the host transcript and SDK. | Good model for explainable event suggestions. This is not a standalone GTA vision detector. The repository explicitly says GTA VI gameplay accuracy is untested. No plugin integration or game-accuracy claim added. |
| [OpenShorts](https://github.com/mutonby/openshorts/tree/b3cd947bc48ad1fc8093bb8f8291b9fb02e9bb2f), MIT | `layout_ranges.py` retains layout metadata so captions and subsequent edits know the composition. `panel_layout.py` filters small/spurious faces. Architecture docs distinguish speaker, split, wide and screencast layouts. Its automatic layout picker can send sampled frames to Gemini. | Keep layouts as persistent data; put captions at the division between regions. Current ClipForge editing stays local and user directed. Automatic per-scene classification would need separate evaluation and an explicit provider choice. |
| [SupoClip](https://github.com/FujiwaraChoki/supoclip/tree/52ec235f11af2ac76de0cc73372b83360b08048f), custom Noncommercial Source License v1.1 | `backend/src/media/reaction_layout.py` requires a stationary corner face and a measurable inset boundary before choosing a reaction layout. The full application also uses queues, transcript analysis and hosted-provider integrations. README says live streams are not supported. | Study behavior, import no implementation. The checked-in license is not MIT and has commercial-use conditions. Replacing ClipForge with this stack would add services and credentials without solving browser live capture. |
| [Cutawan](https://github.com/JeremySNR/cutawan/tree/084d0b2933f64800ee5ca1b22269704f060e65f5), MIT | Electron desktop application. `clipLayout.ts` separates screen content from speaker tracking. `layoutMemory.ts` proposes reusable compositions but explicitly requires review, even for visually similar frames. | Reusable named layouts and preview-before-export are worthwhile. Keep source geometry visible and editable instead of treating a face suggestion as a verified composition. No Codex sign-in/provider integration imported. |
| [Clips Kitty / clips-studio](https://github.com/ColinGPT9/clips-studio/tree/1aa6eed60b250e87aa550ef55b1ec3cad48ee476), AGPL-3.0 | `gaming/layout.py` separates webcam, game and optional UI regions; supports stacked and inset compositions, fit/fill and ordering. It explicitly avoids using motion to identify gameplay because scrolling chat can win. `gaming/framing.py` considers face clearance. This is the project shown in the user's screenshot. | Highest-priority feature: a dedicated webcam/game editor. Implemented independently in ClipForge. No AGPL application code, branding, assets or publishing system was copied. |

Licenses above describe the inspected revisions, not every historical release. Reference source inspection is not a full security audit or an execution test of those applications.

## Implemented in this change

- **Gaming layout** page, reachable from the navigation and video results.
- Independent webcam and gameplay rectangles, drawn on the source or adjusted numerically.
- Claude's integrated local Auto detect proposes a stationary face/webcam region from sampled frames. Manual edits are preserved and switching source cancels stale suggestions. Review the region: a steady game character can still look like a webcam.
- Stacked camera/game, small-camera overlay and game-only compositions; swap order, mirror camera and change camera size.
- Fit the complete selected region to preserve HUD information, or crop to fill it. No stretching.
- Real-time local composition preview, using the backend's exact render plan.
- Named reusable browser layouts; every output also includes the validated geometry in `layout.json`.
- Local upload, saved-video input, and recorded-URL import through the existing downloader.
- Trim up to three minutes, 720p/1080p vertical export, original audio retained, and silent-source support.
- Existing caption styles or No captions; optional English translation. Caption placement can follow the webcam/game division.
- Existing queue, file permissions, retries, library and export handling are reused.
- Viewer clip requests are additional chat evidence: English, French and Russian phrases, distinct-author thresholds, adaptive baseline, negation/spam filtering and fresh-message confirmation. This is a local heuristic, not game-event recognition.

Files: `app/layouts.py`, API/worker integration, optional caption anchor in `app/captions.py`, and `static/gaming.js|css`. The preview never substitutes invented clip or detector status.

## Existing capabilities retained

ClipForge already has word timestamps, multilingual Whisper, light effects, face-following, two-person crops, audio/motion/replay-assisted VOD scoring, duplicate suppression, browser chat buffering, no-caption export and clear paused intros. Copying another application wholesale would duplicate those systems.

## Follow-up work, not claimed complete

1. Evaluate and refine the automatic webcam suggestions on permitted real streams, especially game faces, full-screen cutaways and borderless webcams. Current suggestions need visual review.
2. Per-scene layout changes: a static streamer preset cannot handle every full-screen cutaway or camera switch.
3. GTA event profiles: source-grounded transcript events, with component scores and negation handling, then evaluation on permitted real gameplay. Loudness or motion alone cannot prove a crash or a mission outcome.
4. Reliable additional background platform connectors. Recorded URL support does not establish authenticated Twitch/Kick live monitoring.
5. Multi-hour unattended soak tests and clean-Windows installation tests. No auto-publishing was added.

## Verification

`tests/test_layouts.py` uses generated source regions and actual FFmpeg. It checks output pixel colors, dimensions, trim duration, audio/silent input, ordering, inset placement, whole-region fit, caption rendering, invalid geometry and cross-origin rejection. Its subtitle fixture tests rendering, not recognition accuracy.

`tests/browser_gaming.py` exercises actual local APIs with temporary jobs, crop drawing, preset persistence, mobile layout and a real MP4 export. Delayed suggestion responses are explicitly stubbed for source-switch/manual-edit race checks; those cases do not measure face detection. Reference applications themselves were not installed or executed.

`tests/live_detector.mjs` covers viewer requests alongside reaction spikes, including negation, repeat spam, stable request baselines and stale-message rejection. Server evidence validation accepts the new signal; browser live testing checks the direct gaming-editor link on a captured clip.

An additional real speech check used our existing original generated fixture, cached Whisper small on CPU, and FFmpeg: a 15-second 720×1280 MP4 with audio, 47 timed English words, SRT/VTT and animated captions anchored at the split. No transcript mock was used for that check. Its local evidence is under `artifacts/gaming-smoke-7870d75c/`; this does not validate real GTA gameplay or automatic webcam recognition.
