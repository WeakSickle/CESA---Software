"""
Compression test: how big would a recording be with different settings?

Feeds any video file through the same VideoRecorder the Camera App uses,
as if it were coming from a live camera, and reports file size per setting.

Usage:
    python compression_test.py path\\to\\video.mp4
    python compression_test.py path\\to\\video.mp4 --seconds 30

Results are printed and saved (with the test videos) in a folder called
compression_test_output next to the source video.
"""

import argparse
import csv
import os
import sys
import time
import zipfile

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from camera_core import VideoRecorder, find_ffmpeg, human_size  # noqa: E402

# (label, resolution, fps, encoder, crf, grayscale)
TESTS = [
    ("Old: MPEG-4 (mp4v), 20 fps",        (640, 480), 20, "mp4v",    None, False),
    ("Old: MPEG-4 (mp4v), 5 fps",         (640, 480), 5,  "mp4v",    None, False),
    ("H.264 High, 5 fps",                 (640, 480), 5,  "libx264", 23,   False),
    ("H.264 Medium, 5 fps (default)",     (640, 480), 5,  "libx264", 28,   False),
    ("H.264 Low, 5 fps",                  (640, 480), 5,  "libx264", 32,   False),
    ("H.265 Medium, 5 fps",               (640, 480), 5,  "libx265", 28,   False),
    ("H.264 Medium, 5 fps, B&W",          (640, 480), 5,  "libx264", 28,   True),
    ("H.264 Medium, 5 fps, 320x240",      (320, 240), 5,  "libx264", 28,   False),
]


def read_frames(path, seconds):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        sys.exit(f"Could not open {path}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25
    frames = []
    while len(frames) < seconds * src_fps:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    return frames, src_fps


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--seconds", type=float, default=60, help="how much of the video to use (default 60)")
    args = ap.parse_args()

    if not find_ffmpeg():
        print("WARNING: ffmpeg not found - H.264/H.265 tests will fall back to mp4v.")

    frames, src_fps = read_frames(args.video, args.seconds)
    duration = len(frames) / src_fps
    print(f"Source: {args.video}  ({len(frames)} frames, {src_fps:.0f} fps, {duration:.1f} s, "
          f"{frames[0].shape[1]}x{frames[0].shape[0]})\n")

    out_dir = os.path.join(os.path.dirname(os.path.abspath(args.video)), "compression_test_output")
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for i, (label, size, fps, enc, crf, gray) in enumerate(TESTS):
        path = os.path.join(out_dir, f"test{i + 1}.mp4")
        rec = VideoRecorder(path, size, fps, encoder=enc, crf=crf or 28, grayscale=gray)
        t0 = time.time()
        for n, frame in enumerate(frames):          # pretend frames arrive live from a camera
            rec.write(frame, now=n / src_fps)
        err = rec.close()
        enc_time = time.time() - t0
        if err:
            print(f"{label}: ERROR {err}")
            continue
        size_b = os.path.getsize(path)
        rows.append([label, size_b, size_b / duration * 3600 / 1024 / 1024, enc_time / duration, path])

    # zip test on the old format, to show zipping a video barely helps
    old5 = os.path.join(out_dir, "test2.mp4")
    if os.path.exists(old5):
        zpath = old5 + ".zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            zf.write(old5, os.path.basename(old5))
        zsize = os.path.getsize(zpath)
        rows.insert(2, ["Old: MPEG-4 5 fps + zip", zsize, zsize / duration * 3600 / 1024 / 1024, 0, zpath])

    base = rows[0][1]
    print(f"{'Setting':36s} {'File':>10s} {'vs old 20fps':>13s} {'Per hour':>10s} {'CPU time/real time':>19s}")
    for label, size_b, per_hour, cpu, _ in rows:
        print(f"{label:36s} {human_size(size_b):>10s} {size_b / base * 100:12.1f}% {per_hour:8.0f} MB {cpu:18.2f}x")

    csv_path = os.path.join(out_dir, "compression_results.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["setting", "bytes", "MB_per_hour", "encode_time_per_real_second", "file"])
        w.writerows(rows)
    print(f"\nSaved results to {csv_path}")
    print("CPU time/real time below 1.0 means the computer can keep up in real time.")


if __name__ == "__main__":
    main()
