"""Live captures enter the normal durable, single-GPU job queue."""
import json
import re
import threading
from .. import jobs


_enqueue_lock = threading.Lock()


def enqueue(buffer, event, monitor, info):
    with _enqueue_lock:
        return _enqueue(buffer, event, monitor, info)


def _enqueue(buffer, event, monitor, info):
    pending = [j for j in jobs.list_jobs("autoclip") if j["state"] in ("queued", "running", "receiving")]
    if len(pending) >= 10:
        raise RuntimeError("The clip queue is full (10). New candidates are skipped until an export finishes.")
    source = {"type": "buffer", "platform": "youtube", "url": monitor["url"], "monitor_id": monitor["id"],
              "creator": info.get("creator", ""), "event": event, "captured_at": jobs.now()}
    st = jobs.create("autoclip", source, {k: monitor[k] for k in ("aspect", "preset", "translate")},
                     info.get("title", "Live highlight"), state="receiving")
    job = jobs.Job(st["id"])
    try:
        job.status["source"].update(buffer.snapshot(event["start"], event["end"], job.dir))
        job.status.update(state="queued")
        job.save(); jobs.submit(job.id)
    except Exception as exc:
        job.status.update(state="error", error=str(exc))
        job.save()
        raise
    return job.id


def render(job):
    from .. import video, captions, moments, transcribe
    s, o = job.status, job.status["options"]
    source = s["source"]
    clean = job.dir / "clean.mp4"
    if not job.done("extract") or not clean.exists():
        with job.stage("extract"):
            names = source.get("segments", [])
            if not names or any(not re.fullmatch(r"capture-\d{4}\.ts", n) or not (job.dir / n).is_file() for n in names):
                raise RuntimeError("Capture was interrupted before the video was saved. Record another moment.")
            (job.dir / "capture.txt").write_text("".join(f"file '{n}'\n" for n in names), encoding="utf-8")
            video.run_ffmpeg(["-y", "-f", "concat", "-safe", "1", "-i", "capture.txt",
                "-ss", str(source["offset"]), "-t", str(source["duration"]),
                "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", clean.name],
                job.log, cwd=job.dir, duration=source["duration"], progress=job.progress)
            mi = video.probe(clean)
            job.output(clean, "Original clip · CapCut ready", "video", " (original)", duration=mi.duration,
                       width=mi.width, height=mi.height, detection=source["event"].get("evidence"))
            job.save()  # remains downloadable if transcription fails
    mi = video.probe(clean)
    transcript_path = job.dir / "transcript.json"
    transcript = None
    if o["preset"] != "none":
        if job.done("transcribe") and transcript_path.is_file():
            transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        else:
            with job.stage("transcribe"):
                if not mi.has_audio:
                    raise RuntimeError("Clip has no audio. Download the original, or export again with No captions.")
                video.extract_audio(clean, job.dir / "audio.wav", job.log)
                transcript = transcribe.transcribe(job.dir / "audio.wav", transcript_path, mi.duration,
                    job.log, job.progress, task="translate" if o["translate"] else "transcribe")
                s["language"] = transcript["language"]
    else:
        with job.stage("transcribe"):
            s["stages"]["transcribe"]["detail"] = "Skipped · No captions selected"
    with job.stage("export"):
        rendered = clean
        if o["aspect"] == "vertical":
            rendered = job.dir / "vertical.mp4"
            # Whole frame retained, including webcam and game HUD; no destructive auto crop.
            video.run_ffmpeg(["-y", "-i", str(clean), "-filter_complex", video.blur_fit_graph(),
                "-map", "[v]", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "copy", "-movflags", "+faststart", str(rendered)], job.log,
                duration=mi.duration, progress=job.progress)
            vi = video.probe(rendered)
            job.output(rendered, "Vertical 9:16 · clean", "video", " (vertical)", duration=vi.duration, width=vi.width, height=vi.height)
        if transcript:
            words = captions.shift_words(moments.all_words(transcript), o.get("caption_offset_ms", 0), mi.duration)
            for name, content in (("subtitles.srt", captions.build_srt(words, mi.duration)),
                                  ("subtitles.vtt", captions.build_vtt(words, mi.duration))):
                path = job.dir / name; path.write_text(content, encoding="utf-8")
                job.output(path, name, "text")
            vi = video.probe(rendered)
            jobs.install_fonts(job.dir)
            (job.dir / "captions.ass").write_text(captions.build_ass(words, vi.duration, o["preset"], "bottom", size=(vi.width, vi.height)), encoding="utf-8")
            video.burn_captions(job.dir, rendered.name, "captions.ass", "captioned.mp4", job.log,
                                duration=vi.duration, progress=job.progress)
            job.output(job.dir / "captioned.mp4", "Ready to review · captions", "video", " (captions)",
                       duration=vi.duration, width=vi.width, height=vi.height)
    jobs._finish(job)
