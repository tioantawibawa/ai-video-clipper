"""Daily, read-only shortlist of YouTube sources for tomorrow's clipping."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import time
from zoneinfo import ZoneInfo

from .content_manager import save_json
from .manager_store import ManagerStore
from .pipeline import worker_lock
from .youtube_insights import YouTubeInsights, metrics


def duration_seconds(value):
    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    return sum(int(x or 0)*scale for x, scale in zip(match.groups(), (3600, 60, 1))) if match else 0


def relevant(title, topics):
    title = title.casefold()
    terms = set(t.casefold() for t in topics)
    if "cristiano ronaldo" in terms:
        terms.update(["ronaldo", "cristiano"])
    if "manchester united" in terms:
        terms.update(["man united", "man utd"])
    return any(t in title for t in terms)


def rank_sources(items, config, previous, since, chart_ids, own_channel):
    old = {x["id"]: x for x in previous or []}
    elapsed = time.time()-since if since else None
    rows = []
    direct = config.source_format == 'ronaldo_speaking'
    verified_ids = {s.video_id for s in config.speaking_sources}
    for item in items:
        if direct and item['id'] not in verified_ids:
            continue
        snippet, status = item.get("snippet", {}), item.get("status", {})
        duration = duration_seconds(item.get("contentDetails", {}).get("duration", ""))
        if ((not direct and config.research_scope == "topics" and not relevant(snippet.get("title", ""), config.topics)) or not (60 if direct else 180) <= duration <= 7200
                or snippet.get("channelId") == own_channel or status.get("privacyStatus") != "public"
                or snippet.get("liveBroadcastContent", "none") != "none"):
            continue
        published = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
        if not direct and published < datetime.now(timezone.utc)-timedelta(days=config.lookback_days):
            continue
        language = snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage")
        if not direct and language and not language.lower().startswith(config.language.lower()):
            continue
        row = metrics(item, old.get(item["id"]), elapsed)
        if row["views"] is None:
            continue
        flags = ["Verify event date and claims against actual footage; upload date is not event date"]
        if direct:
            flags.append('Verified Ronaldo speaking; archive from '+snippet['publishedAt'][:10]+'; not current news')
        if not language:
            flags.append("Audio language not confirmed by API metadata")
        if re.search(r"breaking|shock|confirmed|exclusive|\!", row["title"], re.I):
            flags.append("Title makes a strong claim; verify before using it as a hook")
        if not re.search(r"interview|podcast|discuss|talk|debate|reaction|analysis", row["title"], re.I):
            flags.append("Confirm this is spoken commentary suitable for clipping")
        if row["views"] < 100:
            flags.append("Small view sample")
        row.update(channel=snippet.get("channelTitle", ""), duration_seconds=duration,
            language=language or "unconfirmed", license=status.get("license", "unknown"),
            rights_status="cc_label_verify_attribution" if status.get("license") == "creativeCommon" else "permission_required",
            category_id=snippet.get("categoryId", "unknown"),
            source_format='ronaldo_speaking' if direct else 'unverified',
            archival=published < datetime.now(timezone.utc)-timedelta(days=config.lookback_days),
            in_us_chart=item["id"] in chart_ids, checks=flags,
            signal="observed_view_growth" if row["observed_views_per_hour"] is not None else "lifetime_rate_estimate")
        row["ranking_rate"] = row["observed_views_per_hour"] if row["observed_views_per_hour"] is not None else row["lifetime_views_per_hour"]
        rows.append(row)
    return sorted(rows, key=lambda x: x["ranking_rate"] or 0, reverse=True)


class TrendResearch:
    def __init__(self, pipeline, config):
        self.config = config
        self.api = YouTubeInsights(pipeline.accounts[config.account])
        self.root = pipeline.root / "research"
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = ManagerStore(self.root / "research.sqlite3")

    async def collect(self):
        ids, chart_ids, errors = set(), set(), []
        async with self.api.publisher.client() as client:
            await self.api.publisher.authenticate(client)
            own_channel = (await self.api.channel(client))["id"]
            if self.config.source_format == 'ronaldo_speaking':
                ids = sorted({s.video_id for s in self.config.speaking_sources})
                items = []
                for offset in range(0, len(ids), 50):
                    result = await self.api.get(client, 'videos', part='snippet,statistics,status,contentDetails', id=','.join(ids[offset:offset+50]))
                    items.extend(result.get('items', []))
                return items, set(), own_channel, []
            broad = self.config.research_scope == "all"
            seeds = []
            # General chart includes every category; extra charts improve coverage.
            for category in (["0", "17", "20", "22", "24", "25", "28"] if broad else ["17"]):
                try:
                    chart = await self.api.get(client, "videos", part="snippet", chart="mostPopular",
                        regionCode=self.config.region, videoCategoryId=category, maxResults=50)
                    found = chart.get("items", [])
                    chart_ids.update(x["id"] for x in found if broad or relevant(x["snippet"]["title"], self.config.topics))
                    if broad and found:
                        seeds.append(found[0]["snippet"]["title"][:160])
                except Exception as exc:
                    errors.append({"component": "popular_chart", "category": category, "error": type(exc).__name__})
            ids.update(chart_ids)
            cutoff = (datetime.now(timezone.utc)-timedelta(days=self.config.lookback_days)).isoformat()
            queries = list(dict.fromkeys(seeds))[:6]+["podcast interview"] if broad else [t+" interview podcast" for t in self.config.topics]
            for topic in queries:
                modes = [("viewCount", None), ("viewCount", "creativeCommon")] if broad else [("date", None), ("viewCount", None), ("viewCount", "creativeCommon")]
                for order, license_filter in modes:
                    params = dict(part="snippet", type="video", q=topic, order=order,
                        regionCode=self.config.region, relevanceLanguage=self.config.language,
                        publishedAfter=cutoff, maxResults=15)
                    if license_filter:
                        params["videoLicense"] = license_filter
                    try:
                        found = await self.api.get(client, "search", **params)
                        ids.update(x['id']['videoId'] for x in found.get('items', []) if x.get('id', {}).get('videoId'))
                    except Exception as exc:
                        errors.append({"component": "source_search", "topic": topic, "error": type(exc).__name__})
            items = []
            for offset in range(0, len(ids), 50):
                result = await self.api.get(client, "videos", part="snippet,statistics,status,contentDetails",
                    id=",".join(sorted(ids)[offset:offset+50]))
                items.extend(result.get("items", []))
        return items, chart_ids, own_channel, errors

    async def run(self):
        with worker_lock(self.root):
            previous, since = self.store.latest("source_metrics")
            items, chart_ids, own_channel, errors = await self.collect()
            rows = rank_sources(items, self.config, previous, since, chart_ids, own_channel)
            self.store.snapshot("source_metrics", rows)
            tomorrow = str(datetime.now(ZoneInfo("Asia/Jakarta")).date()+timedelta(days=1))
            report = {"generated_at": datetime.now(timezone.utc).isoformat(), "target_date": tomorrow,
                "target_timezone": "Asia/Jakarta", "scope": self.config.research_scope,
                "source_format": self.config.source_format,
                "topics": [] if self.config.research_scope == "all" else self.config.topics, "region": self.config.region,
                "candidates_checked": len(items), "qualified_sources": len(rows),
                "recommendations": rows[:5], "cc_options": [r for r in rows if r["license"] == "creativeCommon"][:3],
                "note": "Sampled research across categories when scope=all; not an exhaustive global trending ranking. US availability does not prove US audience. First sample uses lifetime views/hour; later samples can measure view growth. Recommendations do not authorize downloads or publication.",
                "errors": errors}
            save_json(self.root / "latest.json", report)
            save_json(self.root / (tomorrow+".json"), report)
            self.write_report(self.root / "latest.md", report)
            self.write_report(self.root / (tomorrow+".md"), report)
            return self.root / "latest.json"

    @staticmethod
    def write_report(path, report):
        lines = ["# Rekomendasi clipping untuk "+report["target_date"]+" (WIB)", "",
            "Diperbarui: "+report["generated_at"], "", report["note"], "",
            f"Diperiksa: {report['candidates_checked']} video; lolos filter: {report['qualified_sources']}.", ""]
        for label, rows in [("Rekomendasi utama", report["recommendations"]), ("Alternatif berlabel Creative Commons", report["cc_options"])]:
            lines += ["## "+label, ""]
            if not rows:
                lines += ["Belum ada sumber yang memenuhi filter; tidak mengarang rekomendasi.", ""]
            for index, row in enumerate(rows, 1):
                lines += [f"### {index}. {row['title']}", "", row["url"], "",
                    f"Channel: {row['channel']} | durasi {row['duration_seconds']/60:.1f} menit | bahasa {row['language']}",
                    f"Views: {row['views']:,} | sinyal {row['signal']}: {row['ranking_rate']:.1f} views/jam",
                    f"Lisensi: {row['license']} | status penggunaan: {row['rights_status']}",
                    "Pemeriksaan: "+"; ".join(row["checks"]), ""]
        lines += ["Errors: "+json.dumps(report["errors"]), ""]
        path.write_text("\n".join(lines), encoding="utf-8")


def register_research_cli(app, pipeline_factory, execute):
    import typer
    from .manager_config import ManagerConfig

    @app.command("research")
    def research(config: Path = Path("manager.json")):
        """Research tomorrow's clipping links; never downloads or posts."""
        agent = TrendResearch(pipeline_factory(), ManagerConfig.load(config))
        typer.echo(str(execute(agent.run())))
