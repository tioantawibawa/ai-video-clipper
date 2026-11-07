"""Run with the existing autopilot Python; reuse its OAuth and upload_short only.

Never imports autopilot.py, synthesis, research, or Telegram modules. Stdout is JSON.
"""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import logging
import os
from pathlib import Path


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    pending.replace(path)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def service(root):
    from dotenv import dotenv_values
    env = dotenv_values(root / ".env")
    module = load_module(root / "scripts" / "step4_uploader.py", "clipper_existing_uploader")
    for setting, attr, fallback in [
        ("YOUTUBE_TOKEN_FILE", "TOKEN_FILE", "config/token.json"),
        ("YOUTUBE_CLIENT_SECRETS_FILE", "CLIENT_SECRETS_FILE", "config/client_secrets.json"),
    ]:
        path = Path(env.get(setting) or fallback)
        setattr(module, attr, path if path.is_absolute() else root / path)
    # Preserve the existing authentication/refresh code, but never open a browser
    # from an unattended worker. Interactive login remains the original helper.
    module.CLIENT_SECRETS_FILE = root / "config" / ".clipper-no-interactive-auth"
    youtube = module.get_youtube_service()
    if youtube is None:
        raise ValueError("PENDING_AUTH")
    module.get_youtube_service = lambda: youtube
    return module, youtube


def inspect_channel(youtube, expected):
    try:
        result = youtube.channels().list(part="snippet", mine=True).execute(num_retries=0)
    except Exception as exc:
        if getattr(getattr(exc, "resp", None), "status", None) == 403:
            return {"channel_id": None, "identity_verified": False, "can_read": False}
        raise
    channels = result.get("items", [])
    if len(channels) != 1 or (expected and channels[0]["id"] != expected):
        raise ValueError("Wrong YouTube channel")
    return {"channel_id": channels[0]["id"], "identity_verified": True, "can_read": True,
            "channel_title": channels[0]["snippet"]["title"]}


def verify(youtube, video_id, expected, receipt):
    if receipt.get("privacy") != "private" or (expected and receipt.get("channel_id") != expected):
        raise ValueError("Saved channel/privacy mismatch; ID retained")
    try:
        result = youtube.videos().list(part="status,processingDetails,snippet", id=video_id).execute(num_retries=0)
    except Exception as exc:
        if getattr(getattr(exc, "resp", None), "status", None) != 403:
            raise
        if receipt.get("remote_id") != video_id or receipt.get("privacy") != "private":
            raise ValueError("Read scope missing and no private upload receipt exists")
        return {**receipt, "processing": "unverified", "verification": "insert_response",
                "read_scope_available": False}
    videos = result.get("items", [])
    if not videos:
        return {**receipt, "processing": "pending"}
    video = videos[0]
    if video["status"].get("privacyStatus") != "private" or video["status"].get("publishAt"):
        raise ValueError("Unexpected privacy or scheduled release")
    if expected and video["snippet"]["channelId"] != expected:
        raise ValueError("Wrong YouTube channel")
    return {**receipt, "privacy": "private", "channel_id": video["snippet"]["channelId"],
            "identity_verified": True, "title": video["snippet"]["title"],
            "processing": video.get("processingDetails", {}).get("processingStatus", "pending"),
            "verification": "readback", "read_scope_available": True}


def mirror(root, clip, payload, receipt):
    """Best-effort registration in the existing dashboard; never repeat an upload."""
    try:
        module = load_module(root / "scripts" / "db_manager.py", "clipper_existing_database")
        db = module.DBManager()
        run_id = "clipper_" + receipt["clip_sha256"]
        old = db.get_run(run_id)
        if old and old.get("youtube_id") not in {None, receipt["remote_id"]}:
            return False
        meta = payload["metadata"]
        db.record_run(run_id=run_id, niche="clipper-original", outlier_ref=payload.get("source", ""),
                      title=meta["title"], hook="Original source audio", script_body=json.dumps(meta, ensure_ascii=False),
                      viral_score=0, video_path=str(clip), youtube_id=receipt["remote_id"], privacy_status="PRIVATE")
        return True
    except Exception:
        return False


def execute(args):
    root = args.root.resolve()
    if not (root / "scripts" / "step4_uploader.py").is_file():
        raise ValueError("Existing uploader not found")
    os.chdir(root)  # Original uploader and DB use relative paths in .env.
    module, youtube = service(root)
    identity = inspect_channel(youtube, args.expected_channel)
    if args.operation == "preflight":
        return {"connected": True, "backend": "autopilot", **identity}
    receipt_path = args.receipt.resolve()
    receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {}
    if args.operation == "upload":
        clip = args.clip.resolve()
        with clip.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        payload = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        if payload.get("clip_sha256") and payload["clip_sha256"] != digest:
            raise ValueError("Checksum mismatch")
        if receipt:
            if receipt.get("clip_sha256") != digest or not receipt.get("remote_id"):
                raise ValueError("Invalid existing bridge receipt")
        else:
            metadata = payload["metadata"]
            if metadata.get("publish_at"):
                raise ValueError("Public scheduling is not supported")
            description = metadata["description"]
            missing = [tag for tag in metadata.get("hashtags", []) if tag not in description.split()]
            if missing:
                description += "\n\n" + " ".join(dict.fromkeys(missing))
            uploaded = module.upload_short(clip, metadata["title"], description,
                tags=[tag.lstrip("#") for tag in metadata.get("hashtags", [])],
                privacy_status="private", category_id="22")
            raw = uploaded.get("raw_response", {})
            if not uploaded.get("youtube_id"):
                raise ValueError("Upload returned no video ID")
            # Persist the ID before validation, polling or dashboard writes.
            receipt = {"remote_id": uploaded["youtube_id"], "clip_sha256": digest,
                       "privacy": raw.get("status", {}).get("privacyStatus"),
                       "channel_id": raw.get("snippet", {}).get("channelId"),
                       "title": metadata["title"], "backend": "autopilot",
                       "url": "https://www.youtube.com/watch?v=" + uploaded["youtube_id"],
                       "processing": "pending", "verification": "insert_response"}
            atomic_json(receipt_path, receipt)
            if receipt["privacy"] != "private" or (args.expected_channel and receipt["channel_id"] != args.expected_channel):
                raise ValueError("Upload response channel/privacy mismatch; video ID retained")
        receipt["identity_verified"] = bool(receipt.get("channel_id"))
        receipt["dashboard_registered"] = mirror(root, clip, payload, receipt)
        atomic_json(receipt_path, receipt)
    else:
        if not receipt.get("remote_id"):
            raise ValueError("Missing saved upload ID")
    result = verify(youtube, receipt["remote_id"], args.expected_channel, receipt)
    atomic_json(receipt_path, result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--operation", choices=["preflight", "upload", "status"], required=True)
    p.add_argument("--expected-channel", default="")
    p.add_argument("--clip", type=Path)
    p.add_argument("--manifest", type=Path)
    p.add_argument("--receipt", type=Path)
    args = p.parse_args()
    for name in ("root", "clip", "manifest", "receipt"):
        if getattr(args, name) is not None:
            setattr(args, name, getattr(args, name).resolve())
    logging.disable(logging.CRITICAL)
    try:
        # The imported script must never print tokens, OAuth URLs or HTTP bodies.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = execute(args)
        print(json.dumps(result, ensure_ascii=True), flush=True)
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "status": "needs_attention"}), flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
