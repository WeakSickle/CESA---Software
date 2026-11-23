"""
No-window (headless) recording, for a Raspberry Pi without a desktop or to save power.

Started from camera_ui.py:   python3 camera_ui.py --headless
It uses the settings saved in camera_settings.json (change them in the window version,
or edit the file). Recording starts straight away and keeps going until Ctrl+C
(or until the auto-start service is stopped). If the camera drops out, it keeps
trying to reconnect every few seconds.
"""

import os
import signal
import threading
import time
from datetime import datetime

from camera_core import (CameraReader, SegmentedRecorder, StreamCopyRecorder,
                         describe_source, free_space, human_size, is_network_camera)

RETRY_SECONDS = 5
LOW_SPACE_BYTES = 500 * 1024 * 1024       # warn when less than 500 MB is left


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _saved_message(path):
    size = human_size(os.path.getsize(path)) if os.path.exists(path) else "?"
    free = free_space(os.path.dirname(path))
    msg = f"Saved {os.path.basename(path)} ({size})"
    if free is not None:
        msg += f", free space {human_size(free)}"
        if free < LOW_SPACE_BYTES:
            msg += "  <-- WARNING: storage almost full!"
    return msg


def run_headless(settings, duration=None):
    """Record until stopped. `duration` (seconds) is only used for testing."""
    stop = threading.Event()

    def request_stop(*_):
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)      # sent when the service is stopped

    folder = settings["save_dir"]
    os.makedirs(folder, exist_ok=True)
    source = settings["camera"]
    direct = is_network_camera(source) and settings.get("stream_copy", True)
    seg = settings["segment_minutes"]

    log("Camera App - recording without a window")
    log(f"Camera: {describe_source(source)}")
    if direct:
        log("Mode: direct save of the camera's own video (lowest CPU)")
    else:
        w, h = settings["resolution"]
        log(f"Mode: {w}x{h}, {settings['fps']} fps, {settings['video_format'].split(' (')[0]}, "
            f"quality {settings['quality'].split(' (')[0]}"
            f"{', date/time on video' if settings['timestamp_overlay'] else ''}")
    log(f"New file every {seg:g} minute(s)" if seg > 0 else "One single file (no splitting)")
    log(f"Saving to: {folder}")
    log("Press Ctrl+C to stop.")

    end_time = time.time() + duration if duration else None

    def time_up():
        return end_time is not None and time.time() >= end_time

    if direct:
        _run_stream_copy(settings, folder, stop, time_up)
    else:
        _run_reencode(settings, folder, stop, time_up)
    log("Stopped.")


def _run_stream_copy(settings, folder, stop, time_up):
    reported = set()
    while not stop.is_set() and not time_up():
        rec = StreamCopyRecorder(settings["camera"], folder, settings["file_name_prefix"],
                                 settings["segment_minutes"])
        if rec.error:
            log(f"ERROR: {rec.error}")
            return
        log("Recording...")
        while rec.is_running() and not stop.is_set() and not time_up():
            files = rec.files()
            for path in files[:-1]:                    # every file except the one being written
                if path not in reported:
                    reported.add(path)
                    log(_saved_message(path))
            stop.wait(1)
        problem = rec.last_error() if not rec.is_running() else None
        rec.close()
        for path in rec.files():
            if path not in reported:
                reported.add(path)
                log(_saved_message(path))
        if stop.is_set() or time_up():
            break
        log(f"Camera stream stopped ({problem or 'no reason given'}). "
            f"Retrying in {RETRY_SECONDS} s...")
        stop.wait(RETRY_SECONDS)


def _run_reencode(settings, folder, stop, time_up):
    fps = int(settings["fps"])
    while not stop.is_set() and not time_up():
        reader = CameraReader(settings["camera"], settings["resolution"])
        if not reader.is_opened():
            log(f"Could not open {describe_source(settings['camera'])}. Retrying in {RETRY_SECONDS} s...")
            stop.wait(RETRY_SECONDS)
            continue

        # wait up to 10 s for the first picture
        t0 = time.time()
        while reader.latest()[0] is None and not reader.failed and time.time() - t0 < 10 \
                and not stop.is_set():
            time.sleep(0.1)
        if reader.latest()[0] is None:
            reader.stop()
            log(f"No picture from the camera. Retrying in {RETRY_SECONDS} s...")
            stop.wait(RETRY_SECONDS)
            continue

        recorder = SegmentedRecorder(folder, settings,
                                     on_saved=lambda p, e: log(f"ERROR saving {p}: {e}") if e
                                     else log(_saved_message(p)))
        log("Recording...")
        try:
            next_tick = time.time()
            while not stop.is_set() and not time_up():
                if reader.failed:
                    log(f"Problem: {reader.error}.")
                    break
                frame, _ = reader.latest()
                recorder.write(frame)
                next_tick += 1.0 / fps
                stop.wait(max(0.0, next_tick - time.time()))
        except OSError as e:
            log(f"ERROR while recording: {e}")
        finally:
            recorder.close()
            reader.stop()
        if stop.is_set() or time_up():
            break
        log(f"Reconnecting in {RETRY_SECONDS} s...")
        stop.wait(RETRY_SECONDS)
