"""Official API adapters. Tokens are account-specific environment references.

Non-idempotent requests are deliberately not retried. Uncertain outcomes require
operator reconciliation. YouTube supports account-specific OAuth refresh credentials.
"""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import Account
from .process import run


async def file_chunks(path: Path, size: int = 1024 * 1024):
    with path.open("rb") as stream:
        while chunk := await asyncio.to_thread(stream.read, size):
            yield chunk


def checked(response: httpx.Response) -> dict:
    response.raise_for_status()
    result = response.json()
    error = result.get("error")
    if error and (not isinstance(error, dict) or error.get("code") not in {None, "ok", 0}):
        raise RuntimeError("Platform returned an API error")
    return result


def upload_url(url: str, allowed: str):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or not (
        parsed.hostname == allowed or parsed.hostname.endswith("." + allowed)
    ):
        raise ValueError("Unexpected upload host")
    return url


class Publisher:
    def __init__(self, account: Account):
        self.account = account

    def client(self):
        token = os.environ.get(self.account.token_env)
        if not token and not self.account.refresh_token_env:
            raise ValueError(f"Missing token env for account {self.account.id}")
        proxy = os.environ.get(self.account.proxy_env) if self.account.proxy_env else None
        if self.account.proxy_env and not proxy:
            raise ValueError("Configured proxy environment variable is missing")
        return httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                                 proxy=proxy, timeout=300, follow_redirects=False)

    async def authenticate(self, client):
        if self.account.platform != "youtube" or not self.account.refresh_token_env:
            return
        refs = [self.account.refresh_token_env, self.account.client_id_env, self.account.client_secret_env]
        values = [os.environ.get(ref, "") if ref else "" for ref in refs]
        if not all(values):
            raise ValueError("YouTube offline OAuth credentials are incomplete")
        # Never send an old bearer credential to the OAuth endpoint.
        request = client.build_request("POST", "https://oauth2.googleapis.com/token", data={
            "grant_type": "refresh_token", "refresh_token": values[0],
            "client_id": values[1], "client_secret": values[2]})
        request.headers.pop("Authorization", None)
        result = checked(await client.send(request))
        client.headers["Authorization"] = "Bearer " + result["access_token"]

    async def publish(self, clip: Path, metadata: dict) -> tuple[str, str]:
        async with self.client() as client:
            await self.authenticate(client)
            if self.account.platform == "youtube":
                return await self.youtube(client, clip, metadata)
            if self.account.platform == "tiktok":
                return await self.tiktok(client, clip, metadata)
            return await self.instagram(client, clip, metadata)

    async def youtube(self, client, clip, meta):
        if self.account.privacy not in {"private", "unlisted", "public"}:
            raise ValueError("Invalid YouTube privacy")
        response = await client.post("https://www.googleapis.com/upload/youtube/v3/videos",
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={"X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(clip.stat().st_size)},
            json={"snippet": {"title": meta["title"], "description": meta["description"] + "\n" + " ".join(meta["hashtags"]),
                               "categoryId": "22"}, "status": {"privacyStatus": self.account.privacy}})
        response.raise_for_status()
        url = upload_url(response.headers["Location"], "googleapis.com")
        uploaded = checked(await client.put(url, headers={"Content-Type": "video/mp4",
                           "Content-Length": str(clip.stat().st_size)}, content=file_chunks(clip)))
        # Processing status is polled before marking the job published.
        return "processing", uploaded["id"]

    async def tiktok(self, client, clip, meta):
        root = "https://open.tiktokapis.com/v2"
        if self.account.tiktok_mode == "draft":
            size = clip.stat().st_size
            if not 0 < size <= 64 * 1024 * 1024:
                raise ValueError("TikTok requires a nonempty clip <=64 MiB")
            response = checked(await client.post(root + "/post/publish/inbox/video/init/", json={
                "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                                "chunk_size": size, "total_chunk_count": 1}}))["data"]
            url = upload_url(response["upload_url"], "tiktokapis.com")
            request = client.build_request("PUT", url, headers={"Content-Type": "video/mp4",
                "Content-Length": str(size), "Content-Range": f"bytes 0-{size - 1}/{size}"}, content=file_chunks(clip))
            request.headers.pop("Authorization", None)
            uploaded = await client.send(request)
            uploaded.raise_for_status()
            return "processing", response["publish_id"]
        creator = checked(await client.post(root + "/post/publish/creator_info/query/", json={}))['data']
        privacy = self.account.privacy
        if privacy not in creator["privacy_level_options"]:
            raise ValueError("TikTok privacy must be chosen from creator_info options")
        probe = json.loads(await run("ffprobe", "-v", "error", "-show_format", "-of", "json", str(clip)))
        if float(probe["format"]["duration"]) > creator["max_video_post_duration_sec"]:
            raise ValueError("Clip exceeds TikTok creator duration limit")
        size = clip.stat().st_size
        # A single chunk is valid up to 64 MB. Refuse larger files before init.
        if size > 64 * 1024 * 1024:
            raise ValueError("TikTok adapter requires clips <=64 MiB; lower render bitrate")
        response = checked(await client.post(root + "/post/publish/video/init/", json={
            "post_info": {"title": meta["title"] + " " + " ".join(meta["hashtags"]),
                          "privacy_level": privacy, "disable_duet": True, "disable_stitch": True,
                          "disable_comment": True, "brand_content_toggle": False,
                          "brand_organic_toggle": False},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}}))['data']
        url = upload_url(response["upload_url"], "tiktokapis.com")
        # Upload endpoint uses signed URL; avoid forwarding bearer credentials.
        request = client.build_request("PUT", url, headers={"Content-Type": "video/mp4",
            "Content-Length": str(size), "Content-Range": f"bytes 0-{size - 1}/{size}"}, content=file_chunks(clip))
        request.headers.pop("Authorization", None)
        result = await client.send(request)
        result.raise_for_status()
        return "processing", response["publish_id"]

    async def instagram(self, client, clip, meta):
        account = self.account
        if not account.user_id or not account.public_media_base.startswith("https://"):
            raise ValueError("Instagram requires user_id and an HTTPS public_media_base serving staging files")
        root = f"https://graph.facebook.com/{account.graph_version}"
        result = checked(await client.post(f"{root}/{account.user_id}/media", data={
            "media_type": "REELS", "video_url": account.public_media_base.rstrip("/") + "/" + clip.name,
            "caption": meta["title"] + "\n" + meta["description"] + "\n" + " ".join(meta["hashtags"])}))
        return "processing", result["id"]

    async def poll(self, remote_id: str) -> tuple[str, str]:
        async with self.client() as client:
            await self.authenticate(client)
            account = self.account
            if account.platform == "youtube":
                result = checked(await client.get("https://www.googleapis.com/youtube/v3/videos",
                    params={"part": "processingDetails,status", "id": remote_id}))
                items = result.get("items", [])
                if not items:
                    return "processing", remote_id
                status = items[0].get("processingDetails", {}).get("processingStatus")
                if status == "succeeded":
                    return "published", remote_id
                if status in {"failed", "terminated"}:
                    return "failed", remote_id
                return "processing", remote_id
            if account.platform == "tiktok":
                result = checked(await client.post("https://open.tiktokapis.com/v2/post/publish/status/fetch/",
                                                     json={"publish_id": remote_id}))['data']
                status = result["status"]
                if self.account.tiktok_mode == "draft" and status == "SEND_TO_USER_INBOX":
                    # Terminal handoff, not a published post. The creator finishes in TikTok.
                    return "awaiting_creator", remote_id
                return ("published" if status == "PUBLISH_COMPLETE" else
                        "failed" if status == "FAILED" else "processing"), remote_id
            root = f"https://graph.facebook.com/{account.graph_version}"
            result = checked(await client.get(f"{root}/{remote_id}", params={"fields": "status_code"}))
            status = result.get("status_code")
            if status == "FINISHED":
                # Caller sets uncertain before invoking poll: if this request times out,
                # operator must reconcile instead of blindly publishing twice.
                posted = checked(await client.post(f"{root}/{account.user_id}/media_publish",
                                                   data={"creation_id": remote_id}))
                return "published", posted["id"]
            if status == "PUBLISHED":
                return "published", remote_id
            return ("failed" if status in {"ERROR", "EXPIRED"} else "processing"), remote_id
