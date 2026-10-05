# YouTube research agent

The `research` agent produces tomorrow's clipping shortlist across viral topics when
`research_scope` is `"all"`. The legacy `"topics"` mode restricts discovery to
configured topics; the live research setup uses `"all"`. This scope affects the
research shortlist only, not the separate editorial planner or ads drafts. It runs on the VPS at 12:00 and 18:00 WIB with
up to five minutes jitter. Tomorrow is calculated in Asia/Jakarta; publication
quota remains in America/New_York.

```bash
CLIPPER_DATA_DIR=data/vps CLIPPER_ACCOUNTS_FILE=data/vps/accounts.json \
CLIPPER_SOURCES_FILE=data/vps/sources.json \
.venv/bin/python -m clipper research --config data/vps/manager.json
```

Broad mode uses the official general US `mostPopular` chart plus sports, gaming,
people, entertainment, news and science charts. It searches up to six current
chart-title seeds and one podcast/interview query, including CC results, then
rechecks video details. General-chart discovery has no category restriction.
The bounded sample can miss emerging topics. Broad mode uses at most 14 search
requests (normally 1,400 search quota units) per run; chart failures are reported.
Topic mode retains the existing date/views/CC searches. It excludes owned uploads,
live streams, non-public content, videos outside the configured lookback,
sources under three minutes/over two hours, and explicitly non-English metadata.
Unknown audio language is marked for checking, not silently confirmed English.

Results include five recommendations and up to three alternatives labelled CC,
source links, channels, durations, views, measured/estimated views per hour,
license status and editorial checks. This is sampled research across topics; the chart
is not a complete global trending list or proof of a US-only audience. On the
first run, lifetime views/hour estimates activity, not acceleration. Later
snapshots can calculate observed view growth. Event dates, statements and clip
boundaries must be verified against the actual transcript/footage.

Reports: `data/vps/research/latest.json`, `latest.md` and `YYYY-MM-DD.json/.md`
for the target clipping date. Snapshot history is retained for 90 days. If no
source qualifies, the report says so instead of inventing recommendations.

The laptop worker can copy the latest report every 30 minutes over its approved
SSH key. Set `research_output` in `data/local-worker.json` to the desired local
report directory. The live setup uses `data/recommendations/latest.md`. When
the laptop is asleep, VPS research continues and the local copy catches up on
the next worker start. Report synchronization never creates edit requests.

This agent does not download, edit, post, bypass verification or launch ads.
Standard YouTube license means permission is required before reuse; a CC label
still requires verification and attribution. Editing continues on the laptop.

Install `deploy/clipper-research.service` and `.timer` under
`/etc/systemd/system`, reload systemd and enable the timer. A oneshot service is
normally inactive after completion; verify Result=success and ExecMainStatus=0.

References: [videos.list](https://developers.google.com/youtube/v3/docs/videos/list),
[search.list](https://developers.google.com/youtube/v3/docs/search/list).
