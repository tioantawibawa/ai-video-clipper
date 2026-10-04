# Content manager

The daily manager researches Cristiano Ronaldo and Manchester United, prepares a seven-day editorial calendar, evaluates the authenticated YouTube channel, prepares replies and a YouTube Ads brief. It reuses the existing `openrouter/free -> Gemini -> paid OpenRouter` routing and durable publishing queue. No Google Ads launch or spending endpoint is implemented. Replies default to `reply_mode:draft`; an explicitly authorized `reply_mode:auto` publishes ordinary new replies while holding unsuitable comments. See [laptop editing](laptop-editing.md) for the local rendering/VPS publishing workflow.

## Run

Copy `manager.example.json` to `data/vps/manager.json`. Keep runtime credentials in `.env`; never add them to this JSON or Git.

```bash
python -m clipper manager run --config data/vps/manager.json
python -m clipper manager status --config data/vps/manager.json
python -m clipper manager replies --config data/vps/manager.json
python -m clipper manager reply-review COMMENT_ID --approve --config data/vps/manager.json
python -m clipper manager reply-review COMMENT_ID --reject --config data/vps/manager.json
```

Use the same `CLIPPER_DATA_DIR`, accounts file and sources file as the deployed worker. Reports are saved under `data/vps/manager/YYYY-MM-DD.json` and `latest.json`; comment replies live in `manager.sqlite3`. In draft mode, review the original comment and exact draft text before approving. Auto mode sends only ordinary new replies. A timeout or crash during a send marks the outcome uncertain; inspect YouTube before changing the database, never blindly retry.

Daily runs are idempotent for the account's timezone. `--force` regenerates research and reports, while preserving source-ticket and reply deduplication; auto mode can send newly discovered ordinary replies. Logs contain exception classes rather than credential-containing HTTP errors. Protect reports because they contain comment text. Aggregate snapshots are retained for 90 days; review/delete old comment drafts according to your retention needs.

## Production and upload

Optional autonomous source discovery: enable `produce:true` and `auto_cc_sources:true` to search recent medium-length videos labelled Creative Commons on YouTube. The API license is checked again before creating a ticket and attribution (title, creator, URL, licence information and edit notice) is added to the output description. These are platform license labels, not a guarantee that a third-party uploader owns every element. [YouTube licence documentation](https://support.google.com/youtube/answer/2797468). Keep `video_review:true` for initial editorial checks; change it to false when you want approved-source clips to publish automatically. The daily quota also counts owned videos uploaded manually outside this queue.

Manager production inherits the account's `proxy_env` for source download as well as API access. Store the actual proxy value only in `.env`. A missing configured proxy fails closed. A proxy setting does not solve a CAPTCHA or authenticate a YouTube account; do not use it to bypass a challenge.

Set `produce` to true and list sources that you are allowed to reuse:

```json
{"url":"https://www.youtube.com/watch?v=VIDEO_ID","rights_note":"Owned footage or documented reuse permission reference","approved":true}
```

Research results are ideas, not approved media sources. The manager issues at most one new source ticket per daily run. The existing daemon consumes tickets in `manager/production/`, downloads, transcribes, selects one moment, renders vertical video with subtitles, and queues it. `video_review:true` requires `python -m clipper review JOB_ID --approve` before posting; set it false only for an approved automatic publishing workflow. The campaign account must remain at one post/day. Failed/interrupted source tickets require operator review; after resolving the cause, change their state back to `pending` while the worker is stopped. Download bot challenges are reported as failures; no bypass is implemented. Locally rendered clips can still use the existing outbox.

## Research and analytics limits

Research makes up to two searches per topic over the last seven days and ranks sampled candidates by observed view growth when a previous sample exists, otherwise lifetime views/hour. US region means videos viewable in the US, not proof of a US audience. Results are not an exhaustive/global trend ranking. Plans preserve source links and require editorial fact checking.

Views, likes and comments come from Data API statistics. Engagement proxy is `(likes + comments) / views`, not a unique-viewer rate. Missing counters stay null; samples below 100 views are flagged, not treated as reliable winners. Owned uploads are paginated up to the configured limit. The optional Analytics report requests watch time, average duration/percentage and shares, excluding the latest two days for reporting lag. Enable YouTube Analytics API and authorize `https://www.googleapis.com/auth/yt-analytics.readonly` for these metrics. Missing permissions are reported, not silently replaced with invented retention/CTR. Comment replies additionally need `https://www.googleapis.com/auth/youtube.force-ssl`; authorizing upload alone is insufficient. Any new OAuth permission grant must be completed by the account owner.

Official API references: [search](https://developers.google.com/youtube/v3/docs/search/list), [analytics](https://developers.google.com/youtube/analytics/reference), [replies](https://developers.google.com/youtube/v3/docs/comments/insert).

## VPS schedule

```bash
sudo cp deploy/clipper-manager.service deploy/clipper-manager.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart clipper-vps.service
sudo systemctl enable --now clipper-manager.timer
sudo systemctl start clipper-manager.service
systemctl list-timers clipper-manager.timer
journalctl -u clipper-manager.service -n 30 --no-pager
```

The manager runs daily at 07:15 America/New_York with up to 30 minutes jitter and missed-run recovery. This is the research schedule; publication remains on the existing randomized queue. A oneshot service is normally inactive after successful completion; check `Result=success` and `ExecMainStatus=0`, rather than expecting it to stay active. The persistent publish worker remains active.

Ads reports remain drafts even if `ads_daily_budget_usd` is configured. They include candidate video, objective, US targeting, creative ideas, budget calculation and measurement plan. Actual paid promotion requires the owner's campaign/budget approval, a Google Ads account, billing, and verified promotion rights; adding a budget here does not launch ads.
