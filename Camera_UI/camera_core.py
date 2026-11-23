"""
Camera core: recording logic shared by the window (GUI) and the no-window (headless) mode.

This file does NOT import tkinter or PIL.ImageTk, so it also works on a
Raspberry Pi OS Lite (no desktop) install.

Main pieces:
  - load_settings / save_settings : settings stored in camera_settings.json
  - CameraReader      : reads frames from a USB camera, an IP camera link (rtsp://...)
                        or a video file, in a background thread (always keeps the newest frame)
  - VideoRecorder     : writes frames to ONE video file (H.264 via ffmpeg)
  - SegmentedRecorder : splits a recording into several files (e.g. a new file every 2 minutes)
  - StreamCopyRecorder: for IP cameras, saves the camera's own H.264 video directly
                        (no decoding / re-encoding, so it uses very little CPU)
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime

# keep OpenCV's own technical warnings out of the log (must be set before importing cv2)
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")

import cv2  # noqa: E402

try:
    cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
except AttributeError:
    pass

# ----------------------------------------------------------------------------- constants
DEFAULT_FPS = 5                 # DOC team: 5 fps is enough for monitoring birds
DEFAULT_RESOLUTION = (640, 480)
CAMERA_CHOICES = ["0", "1", "2", "3"]   # 0 = built-in laptop camera, 1+ = USB/HDMI cameras

RESOLUTIONS = ["320x240", "640x480", "800x600", "1024x768", "1280x720", "1920x1080"]
FRAME_RATES = [1, 2, 5, 10, 20, 30]
SEGMENT_CHOICES = {             # label shown in Settings -> minutes (0 = one single file)
    "Off (one file)": 0,
    "1 minute": 1,
    "2 minutes (recommended)": 2,
    "5 minutes": 5,
    "10 minutes": 10,
    "30 minutes": 30,
}

# Video formats shown in Settings -> ffmpeg encoder name ("mp4v" = OpenCV fallback)
VIDEO_FORMATS = {
    "H.264 (recommended)": "libx264",
    "H.265 (a bit smaller, uses more CPU)": "libx265",
    "MPEG-4 (old, largest files)": "mp4v",
}
# Quality -> CRF value (higher number = smaller file, lower quality)
QUALITY_LEVELS = {
    "High (bigger files)": 23,
    "Medium (recommended)": 28,
    "Low (smallest files)": 32,
}
DEFAULT_FORMAT = "H.264 (recommended)"
DEFAULT_QUALITY = "Medium (recommended)"

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera_settings.json")

DEFAULT_SETTINGS = {
    "camera": "0",                      # "0".."3" = USB camera, or an IP camera link like rtsp://...
    "resolution": list(DEFAULT_RESOLUTION),
    "fps": DEFAULT_FPS,
    "video_format": DEFAULT_FORMAT,
    "quality": DEFAULT_QUALITY,
    "grayscale": False,
    "file_name_prefix": "cam",
    "segment_minutes": 2,               # 0 = do not split
    "timestamp_overlay": True,          # draw date and time on the video
    "stream_copy": True,                # IP camera only: save the camera's own video directly
    "save_dir": os.path.join(os.path.expanduser("~"), "Pictures", "CameraApp"),
}


def load_settings(path=SETTINGS_FILE):
    """Read settings from the JSON file. Missing or broken values fall back to defaults."""
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            settings.update({k: v for k, v in saved.items() if k in DEFAULT_SETTINGS})
    except (OSError, ValueError):
        pass
    # basic sanity checks, so a hand-edited file can't crash the app
    if settings["video_format"] not in VIDEO_FORMATS:
        settings["video_format"] = DEFAULT_FORMAT
    if settings["quality"] not in QUALITY_LEVELS:
        settings["quality"] = DEFAULT_QUALITY
    try:
        w, h = (int(x) for x in settings["resolution"])
        settings["resolution"] = [w, h]
    except (TypeError, ValueError):
        settings["resolution"] = list(DEFAULT_RESOLUTION)
    try:
        settings["fps"] = max(1, int(settings["fps"]))
        settings["segment_minutes"] = max(0, float(settings["segment_minutes"]))
    except (TypeError, ValueError):
        settings["fps"], settings["segment_minutes"] = DEFAULT_FPS, 2
    settings["camera"] = str(settings["camera"]).strip() or "0"
    settings["save_dir"] = os.path.expanduser(str(settings["save_dir"]))
    return settings


def save_settings(settings, path=SETTINGS_FILE):
    """Write settings to the JSON file. Returns an error message or None."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        return None
    except OSError as e:
        return str(e)


# ----------------------------------------------------------------------------- helpers
def find_ffmpeg():
    """Return the path to an ffmpeg program, or None if none is available."""
    exe = shutil.which("ffmpeg")            # e.g. installed with apt on a Raspberry Pi
    if exe:
        return exe
    try:
        import imageio_ffmpeg               # pip package that ships ffmpeg for Windows/Mac
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def human_size(num_bytes):
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024


def free_space(folder):
    try:
        return shutil.disk_usage(folder).free
    except OSError:
        return None


def timestamp(now=None):
    return datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d_%H-%M-%S")


def is_usb_camera(source):
    return str(source).strip().isdigit()


def is_network_camera(source):
    return "://" in str(source)


def hide_password(text):
    """Replace user:password in camera links with *** so they don't show up in messages."""
    return re.sub(r"://[^/@\s]+@", "://***@", str(text))


def describe_source(source):
    if is_usb_camera(source):
        return f"camera {source}"
    if is_network_camera(source):
        return f"IP camera {hide_password(source)}"
    return f"video file {os.path.basename(str(source))}"


def _popen_flags():
    return subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0


# ----------------------------------------------------------------------------- camera input
class CameraReader:
    """Reads frames in a background thread and always keeps the newest one.

    Works with a USB camera number ("0", "1"...), an IP camera link (rtsp://, http://)
    or a video file (handy for testing; it is played at normal speed).
    Reading in a thread stops IP camera video from lagging behind.
    """

    def __init__(self, source, resolution=None):
        self.source = str(source).strip()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.frame = None
        self.frame_id = 0
        self.failed = False
        self.error = ""

        if is_usb_camera(self.source):
            backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
            self.cap = cv2.VideoCapture(int(self.source), backend)
            if resolution and self.cap.isOpened():
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, resolution[0])
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, resolution[1])
        else:
            if self.source.lower().startswith("rtsp"):
                # TCP is more reliable than UDP over long cables / Wi-Fi
                os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
            self.cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG)
        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        self.is_file = not is_usb_camera(self.source) and not is_network_camera(self.source)
        self.source_fps = self.cap.get(cv2.CAP_PROP_FPS) if self.cap.isOpened() else 0
        self._thread = None
        if self.cap.isOpened():
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def is_opened(self):
        return self._thread is not None

    def frame_size(self):
        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)) if self.cap else 0
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) if self.cap else 0
        return w, h

    def _run(self):
        delay = 1.0 / self.source_fps if (self.is_file and 0 < self.source_fps < 200) else 0
        fails = 0
        next_time = time.time()
        while not self._stop.is_set():
            ok, frame = self.cap.read()
            if not ok:
                fails += 1
                if self.is_file or fails >= 30:       # live camera: allow ~3 s of hiccups
                    self.failed = True
                    self.error = "end of video file" if self.is_file else "lost connection to the camera"
                    break
                time.sleep(0.1)
                continue
            fails = 0
            with self._lock:
                self.frame = frame
                self.frame_id += 1
            if delay:
                next_time += delay
                time.sleep(max(0, next_time - time.time()))
        self.cap.release()

    def latest(self):
        """Return (frame, frame_id). frame is None until the first frame arrives."""
        with self._lock:
            return self.frame, self.frame_id

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        else:
            self.cap.release()


# ----------------------------------------------------------------------------- recording
class VideoRecorder:
    """Writes frames to a video file at a fixed frame rate and size.

    Call write(frame) as often as you like: frames are written according to the
    real elapsed time, so the saved video always plays back at the correct speed.
    """

    def __init__(self, path, size, fps, encoder="libx264", crf=28, grayscale=False,
                 overlay=False):
        self.path = path
        self.size = (size[0] - size[0] % 2, size[1] - size[1] % 2)   # H.264 needs even sizes
        self.fps = fps
        self.grayscale = grayscale
        self.overlay = overlay
        self.frames_written = 0
        self.start_time = None
        self.proc = None
        self.cv_writer = None
        self.encoder_used = None
        self._stderr = None

        ffmpeg = find_ffmpeg() if encoder != "mp4v" else None
        if ffmpeg:
            w, h = self.size
            cmd = [ffmpeg, "-loglevel", "error", "-y",
                   "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps),
                   "-i", "-", "-an",
                   "-c:v", encoder, "-preset", "veryfast", "-crf", str(crf)]
            if encoder == "libx265":
                cmd += ["-tag:v", "hvc1", "-x265-params", "log-level=error"]
            cmd += ["-pix_fmt", "yuv420p", "-movflags", "+faststart", path]
            self._stderr = tempfile.TemporaryFile()
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                         stderr=self._stderr, creationflags=_popen_flags())
            self.encoder_used = encoder
        else:
            self.cv_writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, self.size)
            self.encoder_used = "mp4v"

    def is_ok(self):
        if self.proc is not None:
            return self.proc.poll() is None
        return self.cv_writer is not None and self.cv_writer.isOpened()

    def prepare(self, frame, now=None):
        """Resize to the recording size, make black & white and add the date/time if chosen."""
        h, w = frame.shape[:2]
        if (w, h) != self.size:
            frame = cv2.resize(frame, self.size, interpolation=cv2.INTER_AREA)
        if self.grayscale:
            frame = cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        if self.overlay:
            frame = frame.copy()
            text = datetime.fromtimestamp(now or time.time()).strftime("%Y-%m-%d %H:%M:%S")
            scale = max(0.4, self.size[0] / 640 * 0.6)
            thick = max(1, int(scale * 2))
            (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
            pad = int(4 * scale) + 1
            x, y = pad, self.size[1] - pad - base
            cv2.rectangle(frame, (0, y - th - pad), (x + tw + pad, self.size[1]), (0, 0, 0), -1)
            cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), thick, cv2.LINE_AA)
        return frame

    def write(self, frame, now=None):
        """Write the frame as many times as needed to keep the video in real time."""
        now = time.time() if now is None else now
        if self.start_time is None:
            self.start_time = now
        target = int((now - self.start_time) * self.fps) + 1
        missing = target - self.frames_written
        if missing <= 0:
            return
        missing = min(missing, max(1, 2 * int(self.fps)))   # don't flood after a long freeze
        frame = self.prepare(frame, now)
        for _ in range(missing):
            if self.proc is not None:
                self.proc.stdin.write(frame.tobytes())      # raises OSError if ffmpeg stopped
            else:
                self.cv_writer.write(frame)
        self.frames_written += missing

    def close(self):
        """Finish the file. Returns an error message, or None if everything is fine."""
        error = None
        if self.proc is not None:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                error = "ffmpeg did not finish in time."
            if self.proc.returncode not in (0, None) and error is None:
                self._stderr.seek(0)
                error = self._stderr.read().decode(errors="replace").strip() or "ffmpeg failed."
            self._stderr.close()
            self.proc = None
        if self.cv_writer is not None:
            self.cv_writer.release()
            self.cv_writer = None
        return error

    def duration(self):
        return self.frames_written / self.fps if self.fps else 0


class SegmentedRecorder:
    """Records into several files: a new file is started every `segment_seconds`.

    Each file is named <prefix>_<date>_<time>.mp4. Finished files are closed in the
    background so no frames are lost while switching files.
    """

    def __init__(self, folder, settings, on_saved=None):
        self.folder = folder
        self.settings = settings
        self.segment_seconds = float(settings["segment_minutes"]) * 60
        self.on_saved = on_saved              # called with (path, error) when a file is finished
        self.current = None
        self.saved_files = []
        self.errors = []
        self._closers = []
        self._lock = threading.Lock()
        self.started_at = time.time()
        os.makedirs(folder, exist_ok=True)

    @property
    def encoder_used(self):
        return self.current.encoder_used if self.current else None

    def _new_file(self, now):
        s = self.settings
        path = os.path.join(self.folder, f"{s['file_name_prefix']}_{timestamp(now)}.mp4")
        rec = VideoRecorder(path, tuple(s["resolution"]), int(s["fps"]),
                            encoder=VIDEO_FORMATS[s["video_format"]],
                            crf=QUALITY_LEVELS[s["quality"]],
                            grayscale=bool(s["grayscale"]),
                            overlay=bool(s["timestamp_overlay"]))
        if not rec.is_ok():
            rec.close()
            raise OSError(f"Could not start recording to {path}")
        return rec

    def _finish(self, rec):
        error = rec.close()
        with self._lock:
            if error:
                self.errors.append(error)
            elif os.path.exists(rec.path):
                self.saved_files.append(rec.path)
        if self.on_saved:
            self.on_saved(rec.path, error)

    def write(self, frame, now=None):
        now = time.time() if now is None else now
        if self.current is not None and self.segment_seconds > 0 \
                and self.current.duration() >= self.segment_seconds:
            old, self.current = self.current, None
            t = threading.Thread(target=self._finish, args=(old,), daemon=True)
            t.start()
            self._closers.append(t)
        if self.current is None:
            self.current = self._new_file(now)
        self.current.write(frame, now)

    def segment_number(self):
        return len(self.saved_files) + len([t for t in self._closers if t.is_alive()]) + (1 if self.current else 0)

    def close(self):
        """Finish the current file and wait for all files to be written."""
        if self.current is not None:
            self._finish(self.current)
            self.current = None
        for t in self._closers:
            t.join(timeout=60)
        return list(self.errors)

    def elapsed(self):
        return time.time() - self.started_at


class StreamCopyRecorder:
    """Saves an IP camera's own video stream directly with ffmpeg (no re-encoding).

    This uses very little CPU, which matters on a Raspberry Pi Zero. Resolution,
    frame rate and quality are whatever the camera is set to.
    """

    def __init__(self, source, folder, prefix, segment_minutes):
        self.source = str(source)
        self.folder = folder
        self.prefix = prefix
        self.started_at = time.time()
        self.proc = None
        self.error = None
        os.makedirs(folder, exist_ok=True)

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            self.error = "ffmpeg is not installed."
            return
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
        if self.source.lower().startswith("rtsp"):
            cmd += ["-rtsp_transport", "tcp"]
        if not is_network_camera(self.source):
            cmd += ["-re"]                        # video file (testing): play at normal speed
        cmd += ["-i", self.source, "-map", "0:v:0", "-c:v", "copy", "-an"]
        seconds = int(float(segment_minutes) * 60)
        if seconds > 0:
            pattern = os.path.join(folder, f"{prefix}_%Y-%m-%d_%H-%M-%S.mp4")
            cmd += ["-f", "segment", "-segment_time", str(seconds), "-reset_timestamps", "1",
                    "-strftime", "1", "-segment_format", "mp4",
                    "-segment_format_options", "movflags=+faststart", pattern]
        else:
            cmd += ["-movflags", "+faststart",
                    os.path.join(folder, f"{prefix}_{timestamp(self.started_at)}.mp4")]
        self._stderr = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=self._stderr, creationflags=_popen_flags())

    def is_running(self):
        return self.proc is not None and self.proc.poll() is None

    def files(self):
        """Files written by this recording so far (oldest first)."""
        found = glob.glob(os.path.join(self.folder, f"{glob.escape(self.prefix)}_*.mp4"))
        found = [f for f in found if os.path.getmtime(f) >= self.started_at - 1]
        return sorted(found)

    def last_error(self):
        if self.proc is None:
            return self.error
        if self.proc.poll() not in (None, 0):
            self._stderr.seek(0)
            text = self._stderr.read().decode(errors="replace").strip()
            lines = [ln for ln in text.splitlines() if ln.strip()]
            return hide_password(lines[-1])[-300:] if lines else "ffmpeg stopped."
        return None

    def close(self):
        """Stop recording cleanly (the last file is finished properly)."""
        if self.proc is None:
            return self.error
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write(b"q")       # ask ffmpeg to finish the file and quit
                self.proc.stdin.flush()
                self.proc.stdin.close()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        error = self.last_error() if self.proc.returncode not in (0, 255) else None
        self._stderr.close()
        return error

    def elapsed(self):
        return time.time() - self.started_at
