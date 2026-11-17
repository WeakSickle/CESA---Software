"""
Simple Camera App (Laptop Version)
----------------------------------
An easy-to-use window for running a camera without writing any code.

Buttons:
  - Start / Stop camera
  - Take Photo        -> saves a .png
  - Start / Stop Recording -> saves a .mp4
  - Settings          -> resolution, frame rate, video format, quality, B&W, file name prefix
  - Choose Save Folder / Open Save Folder

Storage saving:
  Videos are encoded with H.264 through ffmpeg, which makes files roughly 4x smaller
  than OpenCV's built-in "mp4v" encoder at the same frame rate. Frames are always
  resized to the chosen resolution before recording, and written at the chosen frame
  rate based on real time, so the video plays back at the correct speed.
  If ffmpeg is not available, the app falls back to "mp4v" (bigger files).

Requirements: opencv-python, pillow, imageio-ffmpeg  (see requirements.txt)
On a Raspberry Pi you can instead install ffmpeg with:  sudo apt install ffmpeg
"""

import os
import sys
import shutil
import subprocess
import tempfile
import time
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import cv2
from PIL import Image, ImageTk

DEFAULT_FPS = 5                 # DOC team: 5 fps is enough for monitoring birds
DEFAULT_RESOLUTION = (640, 480)
PREVIEW_MAX_WIDTH = 800         # width of the live preview in the window (pixels)
CAMERA_CHOICES = ["0", "1", "2", "3"]   # 0 = built-in laptop camera, 1+ = USB/HDMI cameras

RESOLUTIONS = ["320x240", "640x480", "800x600", "1024x768", "1280x720", "1920x1080"]
FRAME_RATES = [1, 2, 5, 10, 20, 30]

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


# ======================================================================
#  Recording (no GUI code here, so it can also be reused on the Pi)
# ======================================================================
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


class VideoRecorder:
    """Writes frames to a video file at a fixed frame rate and size.

    Call write(frame) as often as you like: frames are written according to the
    real elapsed time, so the saved video always plays back at the correct speed.
    """

    def __init__(self, path, size, fps, encoder="libx264", crf=28, grayscale=False):
        self.path = path
        self.size = (size[0] - size[0] % 2, size[1] - size[1] % 2)   # H.264 needs even sizes
        self.fps = fps
        self.grayscale = grayscale
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
            flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
            self._stderr = tempfile.TemporaryFile()
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                         stderr=self._stderr, creationflags=flags)
            self.encoder_used = encoder
        else:
            self.cv_writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, self.size)
            self.encoder_used = "mp4v"

    def is_ok(self):
        if self.proc is not None:
            return self.proc.poll() is None
        return self.cv_writer is not None and self.cv_writer.isOpened()

    def prepare(self, frame):
        """Resize to the recording size (and make black & white if chosen)."""
        h, w = frame.shape[:2]
        if (w, h) != self.size:
            frame = cv2.resize(frame, self.size, interpolation=cv2.INTER_AREA)
        if self.grayscale:
            frame = cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
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
        frame = self.prepare(frame)
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


def human_size(num_bytes):
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024


# ======================================================================
#  Window (GUI)
# ======================================================================
class CameraApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Camera App")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.fps = DEFAULT_FPS
        self.resolution = DEFAULT_RESOLUTION
        self.video_format = DEFAULT_FORMAT
        self.quality = DEFAULT_QUALITY
        self.grayscale = False
        self.file_name_prefix = "cam"
        self.after_id = None

        self.cap = None            # OpenCV camera object
        self.recorder = None       # VideoRecorder (only while recording)
        self.recording = False
        self.last_frame = None     # most recent frame from the camera
        self.save_dir = os.path.join(os.path.expanduser("~"), "Pictures", "CameraApp")
        os.makedirs(self.save_dir, exist_ok=True)

        self.build_ui()
        self.update_buttons()

    # ------------------------------------------------------------------ UI
    def build_ui(self):
        style = ttk.Style()
        style.configure("Big.TButton", font=("Segoe UI", 12), padding=8)

        # Top row: camera selection
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Camera:", font=("Segoe UI", 11)).pack(side="left")
        self.cam_var = tk.StringVar(value=CAMERA_CHOICES[0])
        self.cam_box = ttk.Combobox(top, textvariable=self.cam_var, values=CAMERA_CHOICES,
                                    width=5, state="readonly")
        self.cam_box.pack(side="left", padx=6)
        ttk.Label(top, text="(0 = laptop camera, try 1 or 2 for an external camera)",
                  foreground="gray").pack(side="left")

        # Video preview area
        self.video = tk.Label(self.root, text="Camera is off.\nClick Start Camera  to begin.",
                              bg="black", fg="white", font=("Segoe UI", 14),
                              width=PREVIEW_MAX_WIDTH // 10, height=22)
        self.video.pack(padx=10, pady=5)

        # Main buttons
        btns = ttk.Frame(self.root, padding=10)
        btns.pack()
        self.start_btn = ttk.Button(btns, text="Start Camera", style="Big.TButton",
                                    command=self.start_camera)
        self.stop_btn = ttk.Button(btns, text="Stop Camera", style="Big.TButton",
                                   command=self.stop_camera)
        self.photo_btn = ttk.Button(btns, text="Take Photo", style="Big.TButton",
                                    command=self.take_photo)
        self.rec_btn = ttk.Button(btns, text="Start Recording", style="Big.TButton",
                                  command=self.toggle_recording)
        self.settings_btn = ttk.Button(btns, text="Settings", style="Big.TButton",
                                       command=self.open_settings)
        for i, b in enumerate([self.start_btn, self.stop_btn, self.photo_btn, self.rec_btn, self.settings_btn]):
            b.grid(row=0, column=i, padx=6)

        # Save folder row
        folder = ttk.Frame(self.root, padding=(10, 0, 10, 5))
        folder.pack(fill="x")
        ttk.Label(folder, text="Saving to:").pack(side="left")
        self.folder_label = ttk.Label(folder, text=self.save_dir, foreground="blue")
        self.folder_label.pack(side="left", padx=6)
        ttk.Button(folder, text="Open Folder", command=self.open_folder).pack(side="right")
        ttk.Button(folder, text="Change Folder…", command=self.choose_folder).pack(side="right", padx=6)

        # Current recording settings (so the user can see them at a glance)
        self.settings_text = tk.StringVar()
        ttk.Label(self.root, textvariable=self.settings_text, foreground="gray",
                  padding=(10, 0)).pack(fill="x")
        self.refresh_settings_text()

        # Status bar
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w",
                  padding=5).pack(fill="x", side="bottom")

    def refresh_settings_text(self):
        w, h = self.resolution
        bw = ", black & white" if self.grayscale else ""
        self.settings_text.set(f"Recording settings: {w}x{h}, {self.fps} fps, "
                               f"{self.video_format.split(' (')[0]}, quality {self.quality.split(' (')[0]}{bw}")

    def update_buttons(self):
        on = self.cap is not None
        self.start_btn.state(["disabled"] if on else ["!disabled"])
        self.cam_box.state(["disabled"] if on else ["!disabled", "readonly"])
        for b in (self.stop_btn, self.photo_btn, self.rec_btn):
            b.state(["!disabled"] if on else ["disabled"])
        self.settings_btn.state(["disabled"] if self.recording else ["!disabled"])
        self.rec_btn.config(text="Stop Recording" if self.recording else "Start Recording")

    # -------------------------------------------------------------- camera
    def start_camera(self):
        index = int(self.cam_var.get())
        self.status.set(f"Opening camera {index}…")
        self.root.update_idletasks()

        # CAP_DSHOW opens much faster on Windows; other systems use the default backend
        backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            cap.release()
            self.status.set(f"Could not open camera {index}.")
            messagebox.showerror("Camera not found",
                                 f"Camera {index} could not be opened.\n\n"
                                 "• Check the camera is plugged in\n"
                                 "• Close other apps using the camera (Teams, Zoom, Meet…)\n"
                                 "• Try a different camera number")
            return

        self.cap = cap
        self.apply_camera_settings()
        self.update_buttons()
        self.update_frame()

    def apply_camera_settings(self):
        # These are only requests: some cameras ignore them. The recording is resized
        # to self.resolution anyway, so the saved file always has the chosen size.
        w, h = self.resolution
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        aw = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        ah = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        note = "" if (aw, ah) == (w, h) else f" (camera gives {aw}x{ah}, recording is resized to {w}x{h})"
        self.status.set(f"Camera on, {self.fps} fps{note}")

    def stop_camera(self):
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        was_recording = self.recording
        if was_recording:
            self.toggle_recording()
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.last_frame = None
        self.video.config(image="", text="Camera is off.\nClick  Start Camera  to begin.")
        self.video.image = None
        if not was_recording:
            self.status.set("Camera stopped.")
        self.update_buttons()

    def update_frame(self):
        self.after_id = None
        if self.cap is None:
            return
        t0 = time.time()
        ok, frame = self.cap.read()
        if not ok:
            self.stop_camera()
            self.status.set("Lost connection to the camera (failed to grab frame).")
            return

        self.last_frame = frame
        if self.recording and self.recorder is not None:
            try:
                self.recorder.write(frame)
                secs = int(self.recorder.duration())
                self.status.set(f"Recording…  {secs // 60:02d}:{secs % 60:02d}")
            except OSError:
                self.toggle_recording()
                messagebox.showerror("Recording stopped", "The video encoder stopped unexpectedly.")

        # Convert OpenCV (BGR) image to something Tkinter can show
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        if img.width > PREVIEW_MAX_WIDTH:
            h = int(img.height * PREVIEW_MAX_WIDTH / img.width)
            img = img.resize((PREVIEW_MAX_WIDTH, h))
        tk_img = ImageTk.PhotoImage(img)
        self.video.config(image=tk_img, text="", width=img.width, height=img.height)
        self.video.image = tk_img   # keep a reference so it isn't garbage-collected

        elapsed_ms = (time.time() - t0) * 1000
        delay = max(1, int(1000 / self.fps - elapsed_ms))
        self.after_id = self.root.after(delay, self.update_frame)

    # ------------------------------------------------------ photo / video
    def timestamp(self):
        return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    def take_photo(self):
        if self.last_frame is None:
            return
        path = os.path.join(self.save_dir, f"{self.file_name_prefix}_photo_{self.timestamp()}.png")
        if cv2.imwrite(path, self.last_frame):
            self.status.set(f"Photo saved: {os.path.basename(path)}")
        else:
            messagebox.showerror("Save failed", f"Could not save photo to:\n{path}")

    def toggle_recording(self):
        if not self.recording:
            if self.last_frame is None:
                return
            path = os.path.join(self.save_dir, f"{self.file_name_prefix}_video_{self.timestamp()}.mp4")
            recorder = VideoRecorder(path, self.resolution, self.fps,
                                     encoder=VIDEO_FORMATS[self.video_format],
                                     crf=QUALITY_LEVELS[self.quality],
                                     grayscale=self.grayscale)
            if not recorder.is_ok():
                recorder.close()
                messagebox.showerror("Recording failed", "Could not start recording.")
                return
            self.recorder, self.recording = recorder, True
            if recorder.encoder_used == "mp4v" and VIDEO_FORMATS[self.video_format] != "mp4v":
                messagebox.showwarning("ffmpeg not found",
                                       "ffmpeg is not installed, so the old MPEG-4 format is used "
                                       "(files will be about 4x bigger).\n\n"
                                       "Run Start_Camera_App.bat again to install it.")
            self.recorder.write(self.last_frame)
        else:
            self.recording = False
            recorder, self.recorder = self.recorder, None
            if recorder is not None:
                self.status.set("Finishing video file…")
                self.root.update_idletasks()
                error = recorder.close()
                if error:
                    messagebox.showerror("Recording problem", f"The video may not have saved correctly:\n\n{error[-500:]}")
                elif os.path.exists(recorder.path):
                    size = human_size(os.path.getsize(recorder.path))
                    self.status.set(f"Video saved: {os.path.basename(recorder.path)}  "
                                    f"({size}, {recorder.duration():.0f} s)")
        self.update_buttons()

    # ------------------------------------------------------------- folder
    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.save_dir, title="Choose where to save")
        if folder:
            self.save_dir = folder
            self.folder_label.config(text=folder)
            self.status.set("Save folder changed.")

    def open_folder(self):
        if sys.platform.startswith("win"):
            os.startfile(self.save_dir)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", self.save_dir])
        else:
            subprocess.Popen(["xdg-open", self.save_dir])

    def open_settings(self):
        if self.recording:
            messagebox.showinfo("Settings", "Stop recording before changing settings.")
            return
        settings_window = tk.Toplevel(self.root)
        settings_window.title("Settings")
        settings_window.transient(self.root)
        settings_window.grab_set()

        def row(r, label, widget, hint=""):
            ttk.Label(settings_window, text=label).grid(row=r, column=0, padx=5, pady=5, sticky="w")
            widget.grid(row=r, column=1, padx=5, pady=5, sticky="w")
            if hint:
                ttk.Label(settings_window, text=hint, foreground="gray").grid(row=r, column=2, padx=5, sticky="w")

        # Resolution
        resolution_var = tk.StringVar(value=f"{self.resolution[0]}x{self.resolution[1]}")
        row(0, "Resolution:", ttk.Combobox(settings_window, textvariable=resolution_var,
                                           values=RESOLUTIONS, state="readonly", width=12),
            "smaller = smaller files")

        # Frame Rate
        frame_rate_var = tk.StringVar(value=str(self.fps))
        row(1, "Frame Rate (fps):", ttk.Combobox(settings_window, textvariable=frame_rate_var,
                                                 values=FRAME_RATES, state="readonly", width=12),
            "5 is enough for birds")

        # Video format
        format_var = tk.StringVar(value=self.video_format)
        row(2, "Video Format:", ttk.Combobox(settings_window, textvariable=format_var,
                                             values=list(VIDEO_FORMATS), state="readonly", width=34))

        # Quality
        quality_var = tk.StringVar(value=self.quality)
        row(3, "Quality:", ttk.Combobox(settings_window, textvariable=quality_var,
                                        values=list(QUALITY_LEVELS), state="readonly", width=34))

        # Black & white
        gray_var = tk.BooleanVar(value=self.grayscale)
        row(4, "Black & White:", ttk.Checkbutton(settings_window, variable=gray_var),
            "slightly smaller files")

        # File name
        file_name_var = tk.StringVar(value=self.file_name_prefix)
        row(5, "File Name Prefix:", ttk.Entry(settings_window, textvariable=file_name_var, width=20))

        # Save Button
        def save_settings():
            w, h = map(int, resolution_var.get().split('x'))
            self.resolution = (w, h)
            self.fps = int(frame_rate_var.get())
            self.video_format = format_var.get()
            self.quality = quality_var.get()
            self.grayscale = gray_var.get()
            prefix = file_name_var.get().strip()
            self.file_name_prefix = "".join(c for c in prefix if c not in '\\/:*?"<>|') or "cam"
            if self.cap is not None:
                self.apply_camera_settings()
            self.refresh_settings_text()
            settings_window.destroy()

        ttk.Button(settings_window, text="Save", command=save_settings).grid(row=6, column=0, columnspan=3, pady=10)

    def on_close(self):
        self.stop_camera()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    CameraApp(root)
    root.mainloop()
