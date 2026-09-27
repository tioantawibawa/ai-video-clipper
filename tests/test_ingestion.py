import pytest

from clipper.downloader import canonical_video, youtube_url


def test_video_canonicalization():
    assert canonical_video("https://youtu.be/abc123?t=30") == canonical_video(
        "https://www.youtube.com/watch?v=abc123&utm_source=feed")


def test_disallow_arbitrary_download_hosts():
    for url in ["http://youtube.com/watch?v=x", "https://youtube.com.evil.test/watch?v=x", "file:///etc/passwd"]:
        with pytest.raises(ValueError):
            youtube_url(url)


def test_reject_channel_in_process():
    with pytest.raises(ValueError):
        canonical_video("https://www.youtube.com/@podcast/videos")
