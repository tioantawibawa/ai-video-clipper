# YouTube download access on the VPS

OAuth for the YouTube Data/Analytics APIs does not authenticate yt-dlp downloads.
Adding `yt-analytics.readonly` to a Cloud project does not change an existing
refresh token. Reauthorize with the upload, readonly, force-ssl and analytics
scopes and save the newly issued refresh token in the protected VPS `.env`.

## Supported downloader dependencies

The pinned `yt-dlp[default]` dependency includes its matching `yt-dlp-ejs` package.
The VPS currently has Node.js 22.23.2; yt-dlp requires Node.js >=22 and explicit
runtime activation. Install the checked-in configuration as the worker user:

```bash
cd ~/ai-video-clipper
.venv/bin/python -m pip install -r requirements.txt
node --version
mkdir -p ~/.config/yt-dlp
# Preserve an existing config; merge the --js-runtimes line if one exists.
cp -n deploy/yt-dlp.conf ~/.config/yt-dlp/config
sudo systemctl restart clipper-vps.service
```

This configuration is for a VPS where Node.js is installed. The Docker image
does not currently bundle Node.js; install a supported runtime separately there.
No on-demand downloads of executable remote components are enabled.

## Human verification remains necessary

Installing EJS solves missing JavaScript support, not an account verification
request. If YouTube says `Sign in to confirm you're not a bot`, stop automated
attempts against that source. A human account owner must complete any YouTube
verification in a browser. Do not rotate identities, proxies or sessions to
evade a challenge, or repeatedly requeue failed production tickets.

After legitimate browser verification, yt-dlp documents using an authorized
browser session or a Netscape-format cookie file. Sessions can expire or be
rejected on another machine/IP; exporting laptop cookies is not a guaranteed
VPS fix. Cookies grant account access: never commit, paste into chat, or place
them in the public staging directory. The current agent does not automatically
export browser sessions. If using a user-supplied cookie file, keep it in
`credentials/` with mode 600 and add its path to the worker user's yt-dlp config.

```text
--cookies /home/ubuntu/ai-video-clipper/credentials/youtube.cookies.txt
```

Prefer an original file supplied by the creator, or a legitimately obtained
local file, when VPS download access is unavailable. Rendered MP4s and their
metadata can use the existing YouTube outbox; that route does not require a
YouTube source download. Preserve source attribution and the daily quota.

Verified on 2026-10-05: adding the matching EJS package and Node runtime removed
the missing runtime condition, but source `O66K_iufELI` still requested human
verification. No new clip was rendered or posted by that check.

Official references:

- [EJS setup](https://github.com/yt-dlp/yt-dlp/wiki/EJS)
- [YouTube extraction/authentication](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#youtube)
- [Cookies and verification](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp)
