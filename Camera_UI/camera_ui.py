"""
Simple Camera App (Laptop Version)
----------------------------------
An easy-to-use window for running a camera without writing any code.

Buttons:
  - Start / Stop camera
  - Take Photo        -> saves a .png
  - Start / Stop Recording -> saves a .mp4
  - Choose Save Folder / Open Save Folder

Requirements: opencv-python, pillow  (see requirements.txt)
"""

import os
import sys
import subprocess
import time
from datetime import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import cv2
from PIL import Image, ImageTk

PREVIEW_MAX_WIDTH = 800        # width of the live preview in the window (pixels)
DEFAULT_FPS = 20               # used if the camera does not report its frame rate
CAMERA_CHOICES = ["0", "1", "2", "3"]   # 0 = built-in laptop camera, 1+ = USB/HDMI cameras


class CameraApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Camera App")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.cap = None            # OpenCV camera object
        self.writer = None         # OpenCV video writer (only while recording)
        self.recording = False
        self.record_start = 0.0
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
        self.video = tk.Label(self.root, text="Camera is off.\nClick  ▶ Start Camera  to begin.",
                              bg="black", fg="white", font=("Segoe UI", 14),
                              width=PREVIEW_MAX_WIDTH // 10, height=22)
        self.video.pack(padx=10, pady=5)

        # Main buttons
        btns = ttk.Frame(self.root, padding=10)
        btns.pack()
        self.start_btn = ttk.Button(btns, text="▶  Start Camera", style="Big.TButton",
                                    command=self.start_camera)
        self.stop_btn = ttk.Button(btns, text="■  Stop Camera", style="Big.TButton",
                                   command=self.stop_camera)
        self.photo_btn = ttk.Button(btns, text="📷  Take Photo", style="Big.TButton",
                                    command=self.take_photo)
        self.rec_btn = ttk.Button(btns, text="⏺  Start Recording", style="Big.TButton",
                                  command=self.toggle_recording)
        for i, b in enumerate([self.start_btn, self.stop_btn, self.photo_btn, self.rec_btn]):
            b.grid(row=0, column=i, padx=6)

        # Save folder row
        folder = ttk.Frame(self.root, padding=(10, 0, 10, 5))
        folder.pack(fill="x")
        ttk.Label(folder, text="Saving to:").pack(side="left")
        self.folder_label = ttk.Label(folder, text=self.save_dir, foreground="blue")
        self.folder_label.pack(side="left", padx=6)
        ttk.Button(folder, text="Open Folder", command=self.open_folder).pack(side="right")
        ttk.Button(folder, text="Change Folder…", command=self.choose_folder).pack(side="right", padx=6)

        # Status bar
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(self.root, textvariable=self.status, relief="sunken", anchor="w",
                  padding=5).pack(fill="x", side="bottom")

    def update_buttons(self):
        on = self.cap is not None
        self.start_btn.state(["disabled"] if on else ["!disabled"])
        self.cam_box.state(["disabled"] if on else ["!disabled", "readonly"])
        for b in (self.stop_btn, self.photo_btn, self.rec_btn):
            b.state(["!disabled"] if on else ["disabled"])
        self.rec_btn.config(text="⏹  Stop Recording" if self.recording else "⏺  Start Recording")

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
        self.status.set(f"Camera {index} is on.")
        self.update_buttons()
        self.update_frame()

    def stop_camera(self):
        if self.recording:
            self.toggle_recording()
        if self.cap is not None:
            self.cap.release()
            self.cap = None
        self.last_frame = None
        self.video.config(image="", text="Camera is off.\nClick  ▶ Start Camera  to begin.")
        self.video.image = None
        self.status.set("Camera stopped.")
        self.update_buttons()

    def update_frame(self):
        if self.cap is None:
            return
        ok, frame = self.cap.read()
        if not ok:
            self.stop_camera()
            self.status.set("Lost connection to the camera (failed to grab frame).")
            return

        self.last_frame = frame
        if self.recording and self.writer is not None:
            self.writer.write(frame)
            secs = int(time.time() - self.record_start)
            self.status.set(f"● Recording…  {secs // 60:02d}:{secs % 60:02d}")

        # Convert OpenCV (BGR) image to something Tkinter can show
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        if img.width > PREVIEW_MAX_WIDTH:
            h = int(img.height * PREVIEW_MAX_WIDTH / img.width)
            img = img.resize((PREVIEW_MAX_WIDTH, h))
        tk_img = ImageTk.PhotoImage(img)
        self.video.config(image=tk_img, text="", width=img.width, height=img.height)
        self.video.image = tk_img   # keep a reference so it isn't garbage-collected

        self.root.after(15, self.update_frame)

    # ------------------------------------------------------ photo / video
    def timestamp(self):
        return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    def take_photo(self):
        if self.last_frame is None:
            return
        path = os.path.join(self.save_dir, f"photo_{self.timestamp()}.png")
        if cv2.imwrite(path, self.last_frame):
            self.status.set(f"Photo saved: {os.path.basename(path)}")
        else:
            messagebox.showerror("Save failed", f"Could not save photo to:\n{path}")

    def toggle_recording(self):
        if not self.recording:
            if self.last_frame is None:
                return
            h, w = self.last_frame.shape[:2]
            fps = self.cap.get(cv2.CAP_PROP_FPS)
            if not fps or fps < 1 or fps > 120:
                fps = DEFAULT_FPS
            path = os.path.join(self.save_dir, f"video_{self.timestamp()}.mp4")
            writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
            if not writer.isOpened():
                messagebox.showerror("Recording failed", "Could not start recording.")
                return
            self.writer, self.recording = writer, True
            self.record_start = time.time()
            self.current_video = path
        else:
            self.recording = False
            if self.writer is not None:
                self.writer.release()
                self.writer = None
            self.status.set(f"Video saved: {os.path.basename(self.current_video)}")
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

    def on_close(self):
        self.stop_camera()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    CameraApp(root)
    root.mainloop()
