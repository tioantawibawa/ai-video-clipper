"""Bounded official YouTube API reads. Never logs OAuth tokens or comment bodies."""
from datetime import datetime, timedelta, timezone
import time
from .publisher import Publisher, checked

ROOT = "https://www.googleapis.com/youtube/v3"


def metrics(item, previous=None, elapsed=None):
    stats, snippet = item.get("statistics", {}), item["snippet"]
    views = int(stats["viewCount"]) if "viewCount" in stats else None
    likes = int(stats["likeCount"]) if "likeCount" in stats else None
    comments = int(stats["commentCount"]) if "commentCount" in stats else None
    age = max(1, (datetime.now(timezone.utc) - datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))).total_seconds()/3600)
    delta = max(0, views - previous["views"]) if previous and views is not None and previous["views"] is not None else None
    return {"id": item["id"], "url": "https://www.youtube.com/watch?v="+item["id"],
            "title": snippet["title"], "channel_id": snippet["channelId"],
            "published_at": snippet["publishedAt"], "views": views, "likes": likes,
            "comments": comments, "engagement_rate": (likes+comments)/views if views and likes is not None and comments is not None else None,
            "lifetime_views_per_hour": views/age if views is not None else None,
            "observed_views_per_hour": delta/(elapsed/3600) if delta is not None and elapsed and elapsed >= 60 else None}


class YouTubeInsights:
    def __init__(self, account):
        self.publisher = Publisher(account)

    async def get(self, client, endpoint, **params):
        return checked(await client.get(ROOT+"/"+endpoint, params=params))

    async def channel(self, client):
        items = (await self.get(client, "channels", part="id,snippet,contentDetails", mine="true"))["items"]
        if len(items) != 1:
            raise ValueError("Expected exactly one authenticated channel")
        return items[0]

    async def video_items(self, client, ids):
        output = []
        for i in range(0, len(ids), 50):
            output += (await self.get(client, "videos", part="snippet,statistics,status", id=",".join(ids[i:i+50]))).get("items", [])
        return output

    async def trends(self, config, previous, since):
        ids = set()
        async with self.publisher.client() as client:
            await self.publisher.authenticate(client)
            cutoff = (datetime.now(timezone.utc)-timedelta(days=config.lookback_days)).isoformat()
            for topic in config.topics:
                for order in ("date", "viewCount"):
                    data = await self.get(client, "search", part="snippet", type="video", q=topic,
                                          regionCode=config.region, relevanceLanguage=config.language,
                                          publishedAfter=cutoff, order=order, maxResults=15)
                    ids.update(x["id"]["videoId"] for x in data.get("items", []))
            items = await self.video_items(client, sorted(ids))
        old = {x["id"]: x for x in previous or []}
        elapsed = time.time()-since if since else None
        rows = [metrics(x, old.get(x["id"]), elapsed) for x in items]
        return sorted(rows, key=lambda x: x["observed_views_per_hour"] if x["observed_views_per_hour"] is not None else (x["lifetime_views_per_hour"] or 0), reverse=True)

    async def owned(self, limit):
        async with self.publisher.client() as client:
            await self.publisher.authenticate(client)
            channel = await self.channel(client)
            playlist = channel["contentDetails"]["relatedPlaylists"]["uploads"]
            ids, page = [], None
            while len(ids) < limit:
                params = {"part": "contentDetails", "playlistId": playlist, "maxResults": min(50, limit-len(ids))}
                if page:
                    params["pageToken"] = page
                result = await self.get(client, "playlistItems", **params)
                ids.extend(x["contentDetails"]["videoId"] for x in result.get("items", []))
                page = result.get("nextPageToken")
                if not page:
                    break
            items = await self.video_items(client, ids)
        return channel["id"], [metrics(x) for x in items]

    async def retention(self):
        end = datetime.now(timezone.utc).date()-timedelta(days=2)
        async with self.publisher.client() as client:
            await self.publisher.authenticate(client)
            response = await client.get("https://youtubeanalytics.googleapis.com/v2/reports", params={
                "ids": "channel==MINE", "startDate": str(end-timedelta(days=28)), "endDate": str(end),
                "dimensions": "video", "metrics": "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,likes,comments,shares",
                "sort": "-views", "maxResults": 50})
            if response.status_code in {401, 403}:
                return {"available": False, "reason": "Enable YouTube Analytics API and authorize yt-analytics.readonly", "http_status": response.status_code}
            result = checked(response)
            return {"available": True, "start_date": str(end-timedelta(days=28)), "end_date": str(end),
                    "columns": [x["name"] for x in result.get("columnHeaders", [])], "rows": result.get("rows", [])}

    async def licensed_sources(self, config):
        """Search medium-length CC-labelled videos; recheck returned license metadata."""
        from .manager_config import ApprovedSource
        result = []
        async with self.publisher.client() as client:
            await self.publisher.authenticate(client)
            channel = await self.channel(client)
            cutoff = (datetime.now(timezone.utc)-timedelta(days=config.lookback_days)).isoformat()
            for topic in config.topics:
                found = await self.get(client, "search", part="snippet", type="video", q=topic,
                    videoLicense="creativeCommon", videoDuration="medium", publishedAfter=cutoff,
                    regionCode=config.region, relevanceLanguage=config.language, order="relevance", maxResults=10)
                ids = [x["id"]["videoId"] for x in found.get("items", [])]
                for item in await self.video_items(client, ids):
                    if item.get("status", {}).get("license") != "creativeCommon" or item["snippet"]["channelId"] == channel["id"]:
                        continue
                    snippet = item["snippet"]
                    url = "https://www.youtube.com/watch?v="+item["id"]
                    attribution = (f"Source: {snippet['title']} by {snippet.get('channelTitle', snippet['channelId'])}; {url}. "
                                   f"Source uploaded: {snippet.get('publishedAt', 'unknown')}; event date not established by upload date. "
                                   "License: Creative Commons Attribution, as labelled on YouTube. "
                                   "License information: https://support.google.com/youtube/answer/2797468. "
                                   "Changes: excerpted, cropped, silence trimmed and subtitled.")
                    result.append(ApprovedSource(url=url, approved=True, attribution=attribution[:1000],
                        rights_note="YouTube Data API status.license=creativeCommon checked "+datetime.now(timezone.utc).isoformat()))
        return result

    async def comments(self, video_id, channel_id, limit):
        async with self.publisher.client() as client:
            await self.publisher.authenticate(client)
            response = await client.get(ROOT+"/commentThreads", params={"part": "snippet", "videoId": video_id,
                                       "order": "time", "textFormat": "plainText", "maxResults": limit})
            if response.status_code == 403:
                reasons = {x.get("reason") for x in response.json().get("error", {}).get("errors", [])}
                if "commentsDisabled" in reasons:
                    return []
                if reasons.intersection({"insufficientPermissions", "forbidden", "quotaExceeded", "dailyLimitExceeded"}):
                    raise PermissionError("Comment access or quota unavailable")
            result = checked(response)
        output = []
        for item in result.get("items", []):
            thread = item["snippet"]
            comment = thread["topLevelComment"]
            body = comment["snippet"]
            # Existing conversations and channel's own comments are not auto-drafted.
            if thread.get("totalReplyCount", 0) or body.get("authorChannelId", {}).get("value") == channel_id:
                continue
            output.append({"id": comment["id"], "video_id": video_id, "text": body["textOriginal"][:2000]})
        return output

    async def reply(self, video_id, comment_id, text):
        async with self.publisher.client() as client:
            await self.publisher.authenticate(client)
            channel = await self.channel(client)
            videos = await self.video_items(client, [video_id])
            if not videos or videos[0]["snippet"]["channelId"] != channel["id"]:
                raise ValueError("Can only reply on the authenticated channel's video")
            parent = (await self.get(client, "comments", part="snippet", id=comment_id)).get("items", [])
            if not parent or parent[0]["snippet"].get("videoId") != video_id:
                raise ValueError("Comment no longer matches reviewed video")
            result = checked(await client.post(ROOT+"/comments", params={"part": "snippet"},
                json={"snippet": {"parentId": comment_id, "textOriginal": text}}))
            return result["id"]
