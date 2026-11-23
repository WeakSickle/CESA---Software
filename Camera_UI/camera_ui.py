"""
Simple Camera App
-----------------
An easy-to-use camera recorder for people with no coding experience.

How to start it:
  python3 camera_ui.py              -> asks: open the window (GUI) or record without a window?
  python3 camera_ui.py --gui        -> opens the window straight away (used by Start_Camera_App.bat)
  python3 camera_ui.py --headless   -> records without a window (saves power / no desktop needed)

Window buttons:
  - Start / Stop camera
  - Take Photo                -> saves a .png
  - Start / Stop Recording    -> saves .mp4 files (a new file every 2 minutes by default)
  - Settings                  -> resolution, frame rate, format, quality, B&W, file name,
                                 file length, date/time on video, direct save for IP cameras
  - Change Folder / Open Folder

Camera can be a USB camera number (0, 1, 2...), an IP camera link (rtsp://...) or a
video file (useful for testing). Settings are remembered in camera_settings.json and
are also used by the no-window mode.

Requirements: opencv-python, pillow, imageio-ffmpeg  (see requirements.txt)
On a Raspberry Pi:  sudo apt install python3-opencv python3-pil.imagetk python3-tk ffmpeg
"""

import argparse
import os
import subprocess
import sys
import time

import cv2

from camera_core import (CAMERA_CHOICES, FRAME_RATES, QUALITY_LEVELS, RESOLUTIONS,
                         SEGMENT_CHOICES, VIDEO_FORMATS, CameraReader, SegmentedRecorder,
                         StreamCopyRecorder, describe_source, human_size, is_network_camera,
                         is_usb_camera, load_settings, save_settings, timestamp)

try:                                   # the window needs tkinter + Pillow; no-window mode does not
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from PIL import Image, ImageTk
    GUI_IMPORT_ERROR = None
except ImportError as e:               # e.g. Raspberry Pi OS Lite without python3-tk
    GUI_IMPORT_ERROR = e

PREVIEW_MAX_WIDTH = 800                # width of the live preview in the window (pixels)


def _label_for_minutes(minutes):
    for label, value in SEGMENT_CHOICES.items():
        if value == minutes:
            return label
    return f"{minutes:g} minutes"


# ======================================================================
#  Window (GUI)
# ======================================================================
class CameraApp:
    def __init__(self, root, settings):
        self.root = root
        self.root.title("Camera App")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.settings = settings
        self.after_id = None

        self.reader = None         # CameraReader (while the camera is on)
        self.recorder = None       # SegmentedRecorder or StreamCopyRecorder (while recording)
        self.recording = False
        self.last_frame = None     # most recent frame from the camera
        self.last_frame_id = 0
        os.makedirs(self.settings["save_dir"], exist_ok=True)

        self.build_ui()
        self.update_buttons()

    # shortcuts to the settings
    @property
    def fps(self):
        return int(self.settings["fps"])

    @property
    def save_dir(self):
        return self.settings["save_dir"]

    def remember(self):
        error = save_settings(self.settings)
        if error:
            self.status.set(f"Could not save settings: {error}")

    # ------------------------------------------------------------------ UI
    def build_ui(self):
        style = ttk.Style()
        style.configure("Big.TButton", font=("Segoe UI", 12), padding=8)

        # Top row: camera selection (a number, or type/paste an IP camera link)
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Camera:", font=("Segoe UI", 11)).pack(side="left")
        self.cam_var = tk.StringVar(value=self.settings["camera"])
        self.cam_box = ttk.Combobox(top, textvariable=self.cam_var, values=CAMERA_CHOICES, width=34)
        self.cam_box.pack(side="left", padx=6)
        ttk.Label(top, text="0 = laptop camera, 1-3 = USB camera,\nor paste an IP camera link (rtsp://...)",
                  foreground="gray").pack(side="left")

        # Video preview area
        self.video = tk.Label(self.root, text="Camera is off.\nClick  Start Camera  to begin.",
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
        for i, b in enumerate([self.start_btn, self.stop_btn, self.photo_btn, self.rec_btn,
                               self.settings_btn]):
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
        self.cam_var.trace_add("write", lambda *_: self.refresh_settings_text())

        # Status bar
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w",
                  padding=5).pack(fill="x", side="bottom")

    def direct_save(self):
        """True when recording will save the IP camera's own video directly."""
        return is_network_camera(self.cam_var.get()) and self.settings["stream_copy"]

    def refresh_settings_text(self):
        s = self.settings
        seg = s["segment_minutes"]
        split = f"new file every {seg:g} min" if seg > 0 else "one single file"
        if self.direct_save():
            self.settings_text.set(f"Recording settings: direct save from IP camera, {split}")
            return
        w, h = s["resolution"]
        extras = (", black & white" if s["grayscale"] else "") + \
                 (", date/time on video" if s["timestamp_overlay"] else "")
        self.settings_text.set(f"Recording settings: {w}x{h}, {s['fps']} fps, "
                               f"{s['video_format'].split(' (')[0]}, quality "
                               f"{s['quality'].split(' (')[0]}{extras}, {split}")

    def update_buttons(self):
        on = self.reader is not None
        self.start_btn.state(["disabled"] if on else ["!disabled"])
        self.cam_box.state(["disabled"] if on else ["!disabled"])
        for b in (self.stop_btn, self.photo_btn, self.rec_btn):
            b.state(["!disabled"] if on else ["disabled"])
        self.settings_btn.state(["disabled"] if self.recording else ["!disabled"])
        self.rec_btn.config(text="Stop Recording" if self.recording else "Start Recording")

    # -------------------------------------------------------------- camera
    def start_camera(self):
        source = self.cam_var.get().strip()
        if not source:
            return
        self.status.set(f"Opening {describe_source(source)}…")
        self.root.update_idletasks()

        reader = CameraReader(source, self.settings["resolution"])
        if not reader.is_opened():
            reader.stop()
            self.status.set(f"Could not open {describe_source(source)}.")
            if is_usb_camera(source):
                tips = ("• Check the camera is plugged in\n"
                        "• Close other apps using the camera (Teams, Zoom, Meet…)\n"
                        "• Try a different camera number")
            elif is_network_camera(source):
                tips = ("• Check the camera has power and its network cable is plugged in\n"
                        "• Check the link, user name and password\n"
                        "• Make sure this computer is on the same network as the camera")
            else:
                tips = "• Check the file name and folder"
            messagebox.showerror("Camera not found",
                                 f"{describe_source(source).capitalize()} could not be opened.\n\n{tips}")
            return

        self.reader = reader
        self.last_frame_id = 0
        self.settings["camera"] = source
        self.remember()
        if source not in CAMERA_CHOICES:
            self.cam_box.config(values=CAMERA_CHOICES + [source])
        self.refresh_settings_text()
        w, h = reader.frame_size()
        tw, th = self.settings["resolution"]
        note = ""
        if (w, h) != (0, 0) and (w, h) != (tw, th) and not self.direct_save():
            note = f" (camera gives {w}x{h}, recording is resized to {tw}x{th})"
        self.status.set(f"Camera on{note}")
        self.update_buttons()
        self.update_frame()

    def stop_camera(self, message=None):
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        was_recording = self.recording
        if was_recording:
            self.toggle_recording()
        if self.reader is not None:
            self.reader.stop()
            self.reader = None
        self.last_frame = None
        self.video.config(image="", text="Camera is off.\nClick  Start Camera  to begin.")
        self.video.image = None
        if message:
            self.status.set(message)
        elif not was_recording:
            self.status.set("Camera stopped.")
        self.update_buttons()

    def update_frame(self):
        self.after_id = None
        if self.reader is None:
            return
        t0 = time.time()
        if self.reader.failed:
            self.stop_camera(f"Camera stopped: {self.reader.error}.")
            return

        frame, frame_id = self.reader.latest()
        if frame is not None:
            self.last_frame = frame
            if self.recording and self.recorder is not None:
                self.record_tick(frame)
            if frame_id != self.last_frame_id:       # only redraw when there is a new picture
                self.last_frame_id = frame_id
                self.show_preview(frame)

        elapsed_ms = (time.time() - t0) * 1000
        delay = max(1, int(1000 / self.fps - elapsed_ms))
        self.after_id = self.root.after(delay, self.update_frame)

    def show_preview(self, frame):
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        if img.width > PREVIEW_MAX_WIDTH:
            h = int(img.height * PREVIEW_MAX_WIDTH / img.width)
            img = img.resize((PREVIEW_MAX_WIDTH, h))
        tk_img = ImageTk.PhotoImage(img)
        self.video.config(image=tk_img, text="", width=img.width, height=img.height)
        self.video.image = tk_img   # keep a reference so it isn't garbage-collected

    # ------------------------------------------------------ photo / video
    def take_photo(self):
        if self.last_frame is None:
            return
        path = os.path.join(self.save_dir, f"{self.settings['file_name_prefix']}_photo_{timestamp()}.png")
        if cv2.imwrite(path, self.last_frame):
            self.status.set(f"Photo saved: {os.path.basename(path)}")
        else:
            messagebox.showerror("Save failed", f"Could not save photo to:\n{path}")

    def record_tick(self, frame):
        rec = self.recorder
        secs = int(rec.elapsed())
        clock = f"{secs // 60:02d}:{secs % 60:02d}"
        if isinstance(rec, StreamCopyRecorder):
            if not rec.is_running():
                problem = rec.last_error()
                self.toggle_recording()
                messagebox.showerror("Recording stopped",
                                     f"The camera stream stopped.\n\n{problem or ''}")
                return
            self.status.set(f"● Recording (direct save)…  {clock}   file {max(1, len(rec.files()))}")
            return
        try:
            rec.write(frame)
            self.status.set(f"● Recording…  {clock}   file {rec.segment_number()}")
        except OSError as e:
            self.toggle_recording()
            messagebox.showerror("Recording stopped", f"The video encoder stopped unexpectedly.\n\n{e}")

    def toggle_recording(self):
        if not self.recording:
            if self.last_frame is None:
                return
            os.makedirs(self.save_dir, exist_ok=True)
            if self.direct_save():
                rec = StreamCopyRecorder(self.reader.source, self.save_dir,
                                         self.settings["file_name_prefix"],
                                         self.settings["segment_minutes"])
                if rec.error:
                    messagebox.showerror("Recording failed", rec.error)
                    return
            else:
                rec = SegmentedRecorder(self.save_dir, self.settings)
                try:
                    rec.write(self.last_frame)
                except OSError as e:
                    rec.close()
                    messagebox.showerror("Recording failed", f"Could not start recording.\n\n{e}")
                    return
                if rec.encoder_used == "mp4v" and VIDEO_FORMATS[self.settings["video_format"]] != "mp4v":
                    messagebox.showwarning("ffmpeg not found",
                                           "ffmpeg is not installed, so the old MPEG-4 format is used "
                                           "(files will be about 4x bigger).\n\n"
                                           "Run Start_Camera_App.bat again to install it.")
            self.recorder, self.recording = rec, True
        else:
            self.recording = False
            rec, self.recorder = self.recorder, None
            if rec is not None:
                self.status.set("Finishing video files…")
                self.root.update_idletasks()
                error = rec.close()
                if isinstance(error, list):
                    error = "\n".join(error)
                files = rec.files() if isinstance(rec, StreamCopyRecorder) else rec.saved_files
                total = sum(os.path.getsize(f) for f in files if os.path.exists(f))
                if error:
                    messagebox.showerror("Recording problem",
                                         f"Some video may not have saved correctly:\n\n{error[-500:]}")
                if files:
                    self.status.set(f"Saved {len(files)} video file(s), {human_size(total)} in total "
                                    f"({int(rec.elapsed())} s). Last: {os.path.basename(files[-1])}")
        self.update_buttons()

    # ------------------------------------------------------------- folder
    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.save_dir, title="Choose where to save")
        if folder:
            self.settings["save_dir"] = folder
            self.folder_label.config(text=folder)
            self.remember()
            self.status.set("Save folder changed.")

    def open_folder(self):
        os.makedirs(self.save_dir, exist_ok=True)
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
        s = self.settings
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.transient(self.root)
        win.grab_set()

        def row(r, label, widget, hint=""):
            ttk.Label(win, text=label).grid(row=r, column=0, padx=5, pady=5, sticky="w")
            widget.grid(row=r, column=1, padx=5, pady=5, sticky="w")
            if hint:
                ttk.Label(win, text=hint, foreground="gray").grid(row=r, column=2, padx=5, sticky="w")

        resolution_var = tk.StringVar(value=f"{s['resolution'][0]}x{s['resolution'][1]}")
        row(0, "Resolution:", ttk.Combobox(win, textvariable=resolution_var,
                                           values=RESOLUTIONS, state="readonly", width=12),
            "smaller = smaller files")

        frame_rate_var = tk.StringVar(value=str(s["fps"]))
        row(1, "Frame Rate (fps):", ttk.Combobox(win, textvariable=frame_rate_var,
                                                 values=FRAME_RATES, state="readonly", width=12),
            "5 is enough for birds")

        format_var = tk.StringVar(value=s["video_format"])
        row(2, "Video Format:", ttk.Combobox(win, textvariable=format_var,
                                             values=list(VIDEO_FORMATS), state="readonly", width=34))

        quality_var = tk.StringVar(value=s["quality"])
        row(3, "Quality:", ttk.Combobox(win, textvariable=quality_var,
                                        values=list(QUALITY_LEVELS), state="readonly", width=34))

        gray_var = tk.BooleanVar(value=s["grayscale"])
        row(4, "Black & White:", ttk.Checkbutton(win, variable=gray_var), "slightly smaller files")

        overlay_var = tk.BooleanVar(value=s["timestamp_overlay"])
        row(5, "Date/Time on Video:", ttk.Checkbutton(win, variable=overlay_var),
            "shows when each moment was filmed")

        segment_var = tk.StringVar(value=_label_for_minutes(s["segment_minutes"]))
        row(6, "New File Every:", ttk.Combobox(win, textvariable=segment_var,
                                               values=list(SEGMENT_CHOICES), state="readonly", width=34),
            "short files = less lost if power fails")

        file_name_var = tk.StringVar(value=s["file_name_prefix"])
        row(7, "File Name Prefix:", ttk.Entry(win, textvariable=file_name_var, width=20),
            "e.g. the site name")

        copy_var = tk.BooleanVar(value=s["stream_copy"])
        row(8, "IP Camera Direct Save:", ttk.Checkbutton(win, variable=copy_var),
            "saves the camera's own video (lowest CPU);\nresolution/fps/quality then come from the camera")

        def save():
            w, h = map(int, resolution_var.get().split("x"))
            s["resolution"] = [w, h]
            s["fps"] = int(frame_rate_var.get())
            s["video_format"] = format_var.get()
            s["quality"] = quality_var.get()
            s["grayscale"] = gray_var.get()
            s["timestamp_overlay"] = overlay_var.get()
            s["segment_minutes"] = SEGMENT_CHOICES.get(segment_var.get(), s["segment_minutes"])
            s["stream_copy"] = copy_var.get()
            prefix = file_name_var.get().strip()
            s["file_name_prefix"] = "".join(c for c in prefix if c not in '\\/:*?"<>|') or "cam"
            self.remember()
            self.refresh_settings_text()
            win.destroy()

        ttk.Button(win, text="Save", command=save).grid(row=9, column=0, columnspan=3, pady=10)

    def on_close(self):
        self.stop_camera()
        self.root.destroy()


# ======================================================================
#  Start-up: window or no window?
# ======================================================================
def has_display():
    if sys.platform.startswith("win") or sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def ask_mode():
    print("Camera App")
    print("  1 = open the camera window (GUI)")
    print("  2 = record without a window (no GUI, saves power)")
    while True:
        try:
            answer = input("Choose 1 or 2 [1]: ").strip()
        except EOFError:
            return "gui"
        if answer in ("", "1"):
            return "gui"
        if answer == "2":
            return "headless"
        print("Please type 1 or 2.")


def run_gui(settings):
    root = tk.Tk()
    CameraApp(root, settings)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="Simple camera recorder.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--gui", action="store_true", help="open the camera window")
    mode.add_argument("--headless", "--no-gui", action="store_true",
                      help="record without a window (uses camera_settings.json)")
    parser.add_argument("--camera", help="camera number (0, 1...), IP camera link (rtsp://...) or video file")
    parser.add_argument("--folder", help="where to save the videos")
    parser.add_argument("--duration", type=float, help="no-window mode: stop after this many seconds (testing)")
    args = parser.parse_args()

    settings = load_settings()
    if args.camera:
        settings["camera"] = args.camera
    if args.folder:
        settings["save_dir"] = os.path.abspath(os.path.expanduser(args.folder))

    if args.gui:
        choice = "gui"
    elif args.headless:
        choice = "headless"
    elif sys.stdin is not None and sys.stdin.isatty():
        choice = ask_mode()
    else:
        choice = "gui" if has_display() else "headless"

    if choice == "gui":
        if GUI_IMPORT_ERROR is not None:
            print(f"The window can't open ({GUI_IMPORT_ERROR}).")
            print("Install it with:  sudo apt install python3-tk python3-pil.imagetk")
            print("Starting without a window instead.\n")
            choice = "headless"
        elif not has_display():
            print("No screen found (are you connected with SSH?). Starting without a window instead.")
            print("Tip: use VNC to see the desktop, then run this again.\n")
            choice = "headless"

    if choice == "gui":
        run_gui(settings)
    else:
        from camera_headless import run_headless
        run_headless(settings, duration=args.duration)


if __name__ == "__main__":
    main()
