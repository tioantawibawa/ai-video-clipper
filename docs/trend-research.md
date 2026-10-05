# YouTube research agent

The `research` agent produces tomorrow's clipping shortlist using the configured
Ronaldo/Manchester United topics. It runs on the VPS at 12:00 and 18:00 WIB with
up to five minutes jitter. Tomorrow is calculated in Asia/Jakarta; publication
quota remains in America/New_York.

```bash
CLIPPER_DATA_DIR=data/vps CLIPPER_ACCOUNTS_FILE=data/vps/accounts.json \
CLIPPER_SOURCES_FILE=data/vps/sources.json \
.venv/bin/python -m clipper research --config data/vps/manager.json
```

Each run uses the official US sports `mostPopular` chart plus recent topic
searches (date, views and Creative Commons), then rechecks video details. Searches
use up to six requests for the default two topics. It excludes owned uploads,
live streams, non-public content, videos outside the configured lookback,
sources under three minutes/over two hours, and explicitly non-English metadata.
Unknown audio language is marked for checking, not silently confirmed English.

Results include five recommendations and up to three alternatives labelled CC,
source links, channels, durations, views, measured/estimated views per hour,
license status and editorial checks. This is sampled niche research; the chart
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
