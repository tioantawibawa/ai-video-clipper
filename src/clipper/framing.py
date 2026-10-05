"""Bounded-cost visual speaker heuristic; deliberately falls back on ambiguity."""
from pathlib import Path
from statistics import median

from .config import Settings
from .models import Moment


def choose_focus(faces: list[tuple[float, float]]) -> tuple[str, list[float]]:
    if len(faces) == 1:
        return "single", [faces[0][0]]
    if len(faces) != 2:
        return "center", [.5]
    ranked = sorted(faces, key=lambda f: f[1], reverse=True)
    if ranked[1][1] > .025 and ranked[0][1] < ranked[1][1] * 1.6:
        return "split", sorted(f[0] for f in faces)
    if ranked[0][1] > .025 and ranked[0][1] > max(.01, ranked[1][1]) * 1.8:
        return "single", [ranked[0][0]]
    return "center", [.5]


def output_time(source_time: float, intervals) -> float:
    offset = 0.0
    for start, end in intervals:
        if source_time <= end:
            return offset + max(0, source_time - start)
        offset += end - start
    return offset


def framing(media: Path, moment: Moment, cfg: Settings, intervals=None) -> str:
    """Compare lower/upper-face motion across 120ms samples to discount camera motion.

    This heuristic is not learned audio-visual diarization. Moving faces, laughter,
    dubbing and camera cuts still require review. Two simultaneously moving mouths
    get stacked panels when consistently observed; low confidence uses center crop.
    """
    if cfg.framing_mode == "fit":
        # Keep gameplay, slides and screen recordings readable without cropping.
        return (f"split=2[fitbg][fitfg];[fitbg]scale={cfg.width}:{cfg.height}:"
                f"force_original_aspect_ratio=increase,crop={cfg.width}:{cfg.height},"
                "boxblur=24:2[fitblur];"
                f"[fitfg]scale={cfg.width}:{cfg.height}:force_original_aspect_ratio=decrease[fitcontent];"
                "[fitblur][fitcontent]overlay=(W-w)/2:(H-h)/2,setsar=1")
    import cv2

    duration = moment.end - moment.start
    intervals = intervals or [(0, duration)]
    capture = cv2.VideoCapture(str(media))
    detector = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    samples = []

    def frame_at(seconds):
        capture.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
        ok, frame = capture.read()
        if not ok:
            return None
        height, width = frame.shape[:2]
        frame = cv2.resize(frame, (640, max(1, round(height * 640 / width))))
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    try:
        for second in range(max(1, int(duration))):
            gray = frame_at(moment.start + second)
            future = frame_at(min(moment.end - .04, moment.start + second + .12))
            if gray is None or future is None:
                samples.append((float(second), "center", [.5]))
                continue
            found = detector.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(35, 35))
            faces = []
            for x, y, width, height in found:
                def difference(y0, y1):
                    a = gray[y + int(height*y0):y + int(height*y1), x:x + width]
                    b = future[y + int(height*y0):y + int(height*y1), x:x + width]
                    return float(cv2.absdiff(a, b).mean()) / 255 if a.size else 0
                # Mouth-specific movement after subtracting upper-face movement.
                motion = max(0, difference(.6, .95) - difference(.1, .45))
                faces.append(((x + width / 2) / gray.shape[1], motion))
            mode, centers = choose_focus(faces)
            samples.append((float(second), mode, centers))
    finally:
        capture.release()

    dual = [centers for _, mode, centers in samples if mode == "split"]
    if len(dual) >= max(2, len(samples) * .4):
        left, right = median(s[0] for s in dual), median(s[1] for s in dual)

        def panel(center):
            return (f"crop=w='min(iw,ih*9/8)':h='min(ih,iw*8/9)':"
                    f"x='max(0,min(iw-ow,iw*{center}-ow/2))':y='(ih-oh)/2',"
                    f"scale={cfg.width}:{cfg.height // 2},setsar=1")

        return f"split=2[p][q];[p]{panel(left)}[l];[q]{panel(right)}[r];[l][r]vstack"

    centers, smoothed = [], .5
    for seconds, mode, focus in samples:
        target = focus[0] if mode == "single" else .5
        smoothed = .6 * smoothed + .4 * target
        centers.append((output_time(seconds, intervals), smoothed))
    expression = f"{centers[-1][1]:.4f}" if centers else "0.5"
    for (start, center), (end, _) in reversed(list(zip(centers, centers[1:]))):
        if end > start:
            expression = f"if(lt(t,{end:.3f}),{center:.4f},{expression})"
    return (f"crop=w='min(iw,ih*9/16)':h='min(ih,iw*16/9)':"
            f"x='max(0,min(iw-ow,iw*({expression})-ow/2))':y='(ih-oh)/2',"
            f"scale={cfg.width}:{cfg.height},setsar=1")
