"""Independent sessions: synthetic providers plus two actual FFmpeg buffers."""
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from app import jobs, video
from app.monitoring import store, processing
from app.monitoring.service import Supervisor, CaptureSlots, Watch
from app.monitoring.buffer import RollingBuffer


class MultiStreamTests(unittest.TestCase):
    def test_watchers_and_slots_are_independent(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(store,"DATA",Path(tmp)):
            store.save_settings({"max_active":2,"buffer_mb":1024,"reserve_gb":2,"quality":"720p"})
            ids=[store.save({"enabled":True,"platform":"youtube","url":f"https://youtube.com/watch?v={i:011d}"}) for i in range(3)]
            slots=CaptureSlots()
            self.assertTrue(slots.acquire(ids[0]));self.assertTrue(slots.acquire(ids[1]));self.assertFalse(slots.acquire(ids[2]))
            slots.release(ids[0]);self.assertTrue(slots.acquire(ids[2]))
            def wait_for_stop(watch):
                watch.report("testing","Synthetic independent watcher")
                watch.stopped.wait(10)
            supervisor=Supervisor()
            with patch.object(Watch,"run",wait_for_stop):
                try:
                    supervisor.start()
                    deadline=time.monotonic()+4
                    while len(supervisor.watches)<3 and time.monotonic()<deadline: time.sleep(.05)
                    self.assertEqual(set(supervisor.watches),set(ids))
                    first=supervisor.watches[ids[0]]
                    c=next(c for c in store.all_monitors() if c["id"]==ids[0]);c.pop("runtime");c.pop("id");c["enabled"]=False;store.save(c,ids[0])
                    self.assertTrue(first.stopped.wait(3))
                    self.assertFalse(supervisor.watches[ids[1]].stopped.is_set())
                finally: supervisor.close()

    def test_two_live_buffers_export_separate_audio_and_video(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(jobs,"JOBS_DIR",Path(tmp)/"jobs"), patch.object(jobs,"submit"):
            root=Path(tmp); buffers=[]
            try:
                for i,color in enumerate(("red","blue")):
                    source=root/f"source{i}.mp4"
                    subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i",f"color={color}:s=320x180:r=30",
                        "-f","lavfi","-i",f"sine=frequency={400+i*500}","-t","8","-c:v","libx264","-g","30","-c:a","aac",str(source)],check=True,capture_output=True)
                    buffer=RollingBuffer(root/f"buffer{i}",min_free=0);buffers.append(buffer);buffer.start(source,realtime=True)
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    if all(b.refresh() and b.segments[-1].end>=2 for b in buffers):break
                    time.sleep(.1)
                self.assertTrue(all(b.process.poll() is None for b in buffers),"Export while both sources are running")
                for i,buffer in enumerate(buffers):
                    mid=f"monitor{i}";event={"start":.2,"end":1.7,"trigger":1}
                    config={"id":mid,"url":"https://youtube.com/watch?v=abcdefghijk","aspect":"landscape","preset":"none","translate":False}
                    jid=processing.enqueue(buffer,event,config,{"title":mid})
                    jobs.run_job(jobs.Job(jid));status=jobs.read_status(jid)
                    self.assertEqual(status["state"],"done");self.assertEqual(status["source"]["monitor_id"],mid)
                    out=jobs.job_dir(jid)/"clean.mp4";self.assertTrue(video.probe(out).has_audio)
                    pixel=subprocess.run(["ffmpeg","-v","error","-i",str(out),"-frames:v","1","-vf","scale=1:1","-pix_fmt","rgb24","-f","rawvideo","pipe:1"],check=True,capture_output=True).stdout
                    self.assertGreater(pixel[0 if i==0 else 2],200)
                buffers[0].close()
                self.assertIsNone(buffers[1].process.poll(),"Stopping one recording must not stop the other")
            finally:
                for buffer in buffers:buffer.discard()


if __name__=="__main__":unittest.main()
