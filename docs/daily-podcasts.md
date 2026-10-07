# Daily Ronaldo podcast Shorts

Editing runs on the Windows laptop. OAuth and publication stay on the Ubuntu VPS.
The local worker reads a manually verified catalog of Ronaldo interview answers, verifies
Creative Commons through YouTube's official API, downloads on the laptop, selects
a complete moment, renders the original voices with English translated subtitles,
then verifies the license again and sends the clip through pinned SSH.
Ronaldo must be the person speaking. Panels, reactions and AI narration are excluded,
even when their titles mention Ronaldo. `speaking_sources` records the video ID and
verified answer interval; only moments fully inside that interval may be rendered.
The catalog is not automatically expanded from search titles. An empty or exhausted
catalog means no post. Update it after checking the actual footage and reuse rights.
Archive dates are shown in the video and description; archives are not current news.

Copy `daily-worker.example.json` to `data/daily-worker.json`, keep the existing
`data/local-worker.json` credential references, and run
`scripts/install-daily-worker.ps1` as the laptop user. Preparation starts at 15:00
and the worker also resumes at login. The laptop must be awake and logged in until
delivery completes; after delivery the VPS can publish independently.

On the VPS, configure the YouTube account with `timezone: Asia/Jakarta`,
`publish_time: 19:10`, `warmed: false`, and `daily_limit: 1`. The daemon claims the
durable queued job when that slot arrives. Upload and processing may take additional
seconds. Late deliveries reserve the next day's slot; missed uploads are rebooked
without a catch-up burst. Accounts without `publish_time` retain randomized slots.

VPS rendering remains disabled. The research/engagement manager can keep running
with `produce: false` because this local worker owns production; that setting no
longer disables the entire production chain.

Check `data/direct-ronaldo-daily/daily-worker.log`, daily receipt JSON, and
`python -m clipper queue` on the VPS. `needs_attention` receipts intentionally do not
retry uncertain transfers or interrupted production. Reconcile remote queue and
outbox files before changing such a receipt. Local failures before delivery can be
retried up to three times; network delivery is never blindly repeated. No new licensed source means no post,
with a logged error instead of unlicensed fallback or duplicate content.

Use speaker framing for direct interviews. An optional verified pixel crop
`portrait_crop: [width, height, x, y]` can remove existing subtitles without cutting
off Ronaldo's face. Face visibility QA runs before transfer. Research configured
with `source_format: ronaldo_speaking` and the same `speaking_sources` catalog excludes
unverified panel discussions and ranks only catalog videos using observed metrics.
