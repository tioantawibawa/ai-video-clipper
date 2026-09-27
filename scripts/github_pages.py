"""Configure the project's documentation site using existing Git credentials.

Credentials stay in memory, are sent only to api.github.com, and are never logged.
Usage: python scripts/github_pages.py status | enable
"""
import argparse
import json
import subprocess

import httpx

REPO = "tioantawibawa/ai-video-clipper"
BRANCH = "codex/ai-video-clipper"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["status", "enable"])
    args = parser.parse_args()
    result = subprocess.run(["git", "credential", "fill"],
        input=f"protocol=https\nhost=github.com\npath={REPO}.git\n\n",
        text=True, capture_output=True, timeout=60, check=True)
    credentials = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    token = credentials.get("password")
    if not token:
        raise SystemExit("No GitHub credential available")
    with httpx.Client(base_url="https://api.github.com", timeout=30, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }) as client:
        repo = client.get(f"/repos/{REPO}")
        repo.raise_for_status()
        info = repo.json()
        print(json.dumps({"repository": info["full_name"], "private": info["private"],
                          "default_branch": info["default_branch"], "permissions": info.get("permissions")}))
        pages = client.get(f"/repos/{REPO}/pages")
        if args.action == "enable":
            # Publish only the documentation directory, never source credentials/data.
            payload = {"source": {"branch": BRANCH, "path": "/docs"}, "build_type": "legacy"}
            if pages.status_code == 404:
                pages = client.post(f"/repos/{REPO}/pages", json=payload)
            elif pages.is_success:
                existing = pages.json()
                if existing.get("source") != payload["source"]:
                    raise SystemExit("Existing Pages source differs; refusing to overwrite it")
            pages.raise_for_status()
        if pages.is_success:
            data = pages.json()
            print(json.dumps({"pages_url": data.get("html_url"), "status": data.get("status"),
                              "source": data.get("source")}))
            build = client.get(f"/repos/{REPO}/pages/builds/latest")
            if build.is_success:
                print(json.dumps({"latest_build_status": build.json().get("status")}))
        else:
            print(json.dumps({"pages_http_status": pages.status_code}))


if __name__ == "__main__":
    main()
