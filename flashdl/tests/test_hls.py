import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.hls import HlsDownloader, find_ffmpeg

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001/index.m3u8"
print("ffmpeg :", find_ffmpeg())

dest = os.path.join(tempfile.mkdtemp(), "test.mp4")
dl = HlsDownloader(url, dest, n_threads=4)
try:
    dl.run()
    print("OK ->", dl.dest, os.path.getsize(dl.dest), "octets")
except Exception as e:
    print("ERREUR :", type(e).__name__, e)