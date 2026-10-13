import json
import os
import sys
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from core.api_server import start_api


class BrowserVideoApiTests(unittest.TestCase):
    def setUp(self):
        self.received = []

        def on_download_request(payload):
            self.received.append(payload)
            return {"ok": True, "id": "video-1"}

        self.server = start_api(object(), port=0,
                                on_download_request=on_download_request)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_ping_reports_api_version_for_extension_compatibility(self):
        with urlopen(self.base + "/ping", timeout=3) as response:
            self.assertEqual(response.status, 200)
            payload = json.loads(response.read())
        self.assertEqual(payload, {"app": "Roch Xd", "version": "1.2.0"})

    def post(self, origin):
        body = json.dumps({
            "url": "https://media.example/video.mp4",
            "filename": "video.mp4",
            "referer": "https://site.example/watch",
            "mediaType": "video",
        }).encode()
        request = Request(self.base + "/add", data=body, method="POST",
                          headers={"Content-Type": "application/json",
                                   "Origin": origin})
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()

    def test_extension_origin_routes_video_to_existing_add_callback(self):
        status, headers, payload = self.post(
            "chrome-extension://abcdefghijklmnopabcdefghijklmnop")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["id"], "video-1")
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0]["url"],
                         "https://media.example/video.mp4")
        self.assertEqual(self.received[0]["referer"],
                         "https://site.example/watch")
        self.assertEqual(headers.get("Access-Control-Allow-Origin"),
                         "chrome-extension://abcdefghijklmnopabcdefghijklmnop")

    def test_web_page_origin_is_rejected_before_add_callback(self):
        status, _, _ = self.post("https://site.example")
        self.assertEqual(status, 403)
        self.assertEqual(self.received, [])

    def test_selected_youtube_format_reuses_recent_extract_result(self):
        from core.extractors import VideoFormat, VideoInfo

        info = VideoInfo(success=True, title="Cours fiable", extractor="youtube",
                         formats=[VideoFormat(label="720p", ext="mp4",
                                              url="https://cdn.example/720.mp4",
                                              format_id="720")])
        origin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
        extract_body = {"url": "https://www.youtube.com/watch?v=example",
                        "referer": "https://www.youtube.com/watch?v=example",
                        "cookies": "sid=test", "userAgent": "TestAgent"}
        add_body = {**extract_body, "formatIndex": 0,
                    "page_title": "Cours fiable", "media_type": "video"}
        def send(path, body):
            request = Request(self.base + path, data=json.dumps(body).encode(),
                              method="POST", headers={
                                  "Content-Type": "application/json", "Origin": origin})
            with urlopen(request, timeout=3) as response:
                return json.loads(response.read())

        with patch("core.api_server.extract", return_value=info) as extractor:
            self.assertTrue(send("/extract", extract_body)["ok"])
            self.assertTrue(send("/add", add_body)["ok"])
        self.assertEqual(extractor.call_count, 1)
        self.assertIs(self.received[0]["_video_info"], info)


if __name__ == "__main__":
    unittest.main()
