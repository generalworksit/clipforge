"""Publishing contract tests. Google responses are mocked; nothing is uploaded."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch, Mock
import requests
from fastapi.testclient import TestClient
from app import main,jobs
from app.publishing import store,youtube
from app.publishing.worker import Publisher


def response(code=200,body=None,headers=None):
    r=Mock(status_code=code,headers=headers or {});r.json.return_value=body or {};return r


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.patches=[patch.object(store,"DATA",self.root/"publishing"),patch.object(jobs,"JOBS_DIR",self.root/"jobs")]
        for p in self.patches:p.start()
        self.client=TestClient(main.app,base_url="http://127.0.0.1:8000")
        j=jobs.create("live",{},{});self.job=jobs.Job(j["id"])
        self.source=self.job.dir/"highlight.mp4";self.source.write_bytes(b"explicit synthetic transport fixture"*100)
        self.job.output(self.source,"Fixture","video");jobs._finish(self.job)
        self.body={"job_id":self.job.id,"file":"highlight.mp4","title":"Reviewed fixture","description":"","privacy":"private"}
        self.connected={"connected":True,"configured":True,"channel_id":"fixture-channel","title":"Fixture account"}

    def tearDown(self):
        self.client.close()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def draft(self):
        r=self.client.post("/api/publishing/items",json=self.body)
        self.assertEqual(r.status_code,201,r.text);return r.json()

    def test_draft_snapshot_approval_binding_duplicates_and_cancel(self):
        d=self.draft();self.assertEqual(d["state"],"draft")
        self.assertEqual(store.media(d["id"]).read_bytes(),self.source.read_bytes())
        with patch.object(youtube,"upload") as upload:self.assertFalse(Publisher().step());upload.assert_not_called()
        self.assertEqual(self.client.post("/api/publishing/items",json=self.body).status_code,409)
        with patch.object(youtube,"account",return_value=self.connected):
            route=f"/api/publishing/items/{d['id']}/approve"
            self.assertEqual(self.client.post(route,json={"channel_id":"wrong","consent":True}).status_code,400)
            self.assertEqual(self.client.post(route,json={"channel_id":"fixture-channel","consent":False}).status_code,400)
            self.assertEqual(self.client.post(route,json={"channel_id":"fixture-channel","consent":True}).status_code,200)
            self.assertEqual(self.client.post(route,json={"channel_id":"fixture-channel","consent":True}).status_code,409)
        self.assertEqual(self.client.post(f"/api/publishing/items/{d['id']}/cancel").status_code,200)
        self.assertFalse(store.media(d["id"]).exists());self.assertTrue(self.source.exists())

    def test_validation_and_origin(self):
        for changes in ({"file":"../source.mp4"},{"privacy":"friends"},{"title":"<bad>"},{"publish_at":time.time()-1}):
            self.assertEqual(self.client.post("/api/publishing/items",json=self.body|changes).status_code,400)
        self.assertEqual(self.client.post("/api/publishing/items",json=self.body,headers={"Origin":"https://foreign.example"}).status_code,403)
        self.assertEqual(self.client.put("/api/publishing/settings",json={"daily_limit":0}).status_code,422)
        at=time.time()+3600
        data=youtube.metadata(self.body|{"made_for_kids":False,"synthetic":False,"publish_at":at,"privacy":"public"})
        self.assertEqual(data["status"]["privacyStatus"],"private");self.assertIn("publishAt",data["status"])

    def test_pkce_state_and_encrypted_tokens(self):
        youtube.configure({"installed":{"client_id":"fixture.apps.googleusercontent.com","client_secret":"fixture-secret"}})
        url=youtube.begin("http://127.0.0.1:8000/api/publishing/youtube/callback")
        query=parse_qs(urlparse(url).query);self.assertEqual(query["code_challenge_method"],["S256"])
        with patch.object(youtube.requests,"post") as post:
            with self.assertRaises(ValueError):youtube.finish("wrong-state","code")
            post.assert_not_called()
        token={"access_token":"fixture-access","refresh_token":"fixture-refresh","expires_in":3600}
        with patch.object(youtube.requests,"post",return_value=response(body=token)),patch.object(youtube.requests,"get",return_value=response(body={"items":[{"id":"fixture-channel","snippet":{"title":"Fixture"}}]})):
            self.assertTrue(youtube.finish(query["state"][0],"fixture-code")["connected"])
        self.assertNotIn("fixture-refresh",self.client.get("/api/publishing").text)
        self.assertEqual(youtube.token("fixture-channel"),"fixture-access")
        with self.assertRaises(youtube.ReviewRequired):youtube.token("another-channel")

    def test_lost_final_ack_resumes_without_duplicate_insert(self):
        d=self.draft();store.update(d["id"],state="approved",channel_id="fixture-channel")
        url="https://www.googleapis.com/upload/youtube/v3/videos?upload_id=fixture"
        worker=Publisher()
        with patch.object(youtube,"token",return_value="test-token"),patch.object(youtube.requests,"post",return_value=response(headers={"Location":url})) as post,patch.object(youtube.requests,"put",side_effect=[response(308),requests.ConnectionError("test only")]) as put:
            self.assertTrue(worker.step());self.assertEqual(post.call_count,1);self.assertEqual(put.call_count,2)
        self.assertEqual(store.get(d["id"])["state"],"retry")
        self.assertTrue(store.get(d["id"])["session"])
        store.update(d["id"],next_try=0)
        worker=Publisher()  # A new worker resumes the session persisted by the previous one.
        with patch.object(youtube,"token",return_value="test-token"),patch.object(youtube.requests,"post") as post,patch.object(youtube.requests,"put",return_value=response(body={"id":"abcdefghijk"})) as put:
            self.assertTrue(worker.step());post.assert_not_called();self.assertEqual(put.call_count,1)
        self.assertEqual(store.get(d["id"])["state"],"uploaded")
        self.assertNotIn("upload_id",self.client.get("/api/publishing").text)

    def test_changed_media_and_daily_limit(self):
        d=self.draft();item=store.update(d["id"],state="approved",channel_id="fixture-channel")
        store.media(d["id"]).write_bytes(b"changed")
        with patch.object(youtube.requests,"post") as post:
            with self.assertRaises(youtube.ReviewRequired):youtube.upload(item,threading.Event())
            post.assert_not_called()
        store.update(d["id"],state="uploaded",uploaded_at=time.time());store.save_settings({"daily_limit":1})
        self.source.write_bytes(b"another-fixture")
        second=self.draft();store.update(second["id"],state="approved",channel_id="fixture-channel")
        self.assertIsNone(store.claim())

    def test_expired_session_needs_remote_review_before_removal(self):
        d=self.draft();store.update(d["id"],state="needs_review",channel_id="original-channel",session=youtube.seal("https://www.googleapis.com/upload?upload_id=fixture"))
        with patch.object(youtube,"account",return_value=self.connected):
            self.assertEqual(self.client.post(f"/api/publishing/items/{d['id']}/approve",json={"channel_id":"fixture-channel","consent":True}).status_code,409)
        route=f"/api/publishing/items/{d['id']}/cancel"
        self.assertEqual(self.client.post(route).status_code,409)
        self.assertEqual(self.client.post(route,json={"checked_remote":True}).status_code,200)
        for url in ["https://foreign.example/upload","https://:password@www.googleapis.com/upload"]:
            with self.assertRaises(youtube.ReviewRequired):youtube.session_url(url)


if __name__=="__main__":unittest.main()
