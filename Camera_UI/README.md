# Camera App

A simple camera recorder for people with no coding experience. It can run **with a window**
(buttons) or **without a window** (records in the background, uses less power).

## Starting the app

| Where | How |
|---|---|
| Windows laptop | Double-click **`Start_Camera_App.bat`** (first time installs what it needs) |
| Raspberry Pi | `python3 camera_ui.py` then choose **1** (window) or **2** (no window) |
| Raspberry Pi, straight to no window | `python3 camera_ui.py --headless` (stop with **Ctrl+C**) |

The window needs a screen (desktop or VNC). Over SSH, or on a Pi without a desktop,
the app automatically records without a window.

## Choosing the camera

In the **Camera** box at the top of the window:

- `0` = laptop camera, `1`-`3` = USB camera
- or paste an **IP camera link**, e.g. `rtsp://user:password@192.168.1.10:554/stream1`
- or a video file path (handy for testing without a camera)

## Buttons

- **Start Camera / Stop Camera** – live view on/off
- **Take Photo** – saves a `.png`
- **Start Recording / Stop Recording** – saves `.mp4` files. By default a **new file starts
  every 2 minutes**, named with the date and time, e.g. `cam_2026-11-24_10-15-30.mp4`.
  If power is lost, only the last few minutes are affected.
- **Settings** – see below. Settings are remembered (in `camera_settings.json`) and are also
  used by the no-window mode and the auto-start.
- **Change Folder / Open Folder** – where files are saved (default: `Pictures/CameraApp`)

## Settings

| Setting | Meaning | Recommended |
|---|---|---|
| Resolution | Picture size. Smaller = smaller files | 640x480 |
| Frame Rate | Pictures per second | 5 (enough for birds) |
| Video Format | How the video is compressed | H.264 |
| Quality | Medium is a good balance, Low = smallest files | Medium |
| Black & White | Slightly smaller files | Off |
| Date/Time on Video | Prints the date and time in the corner of the video | On |
| New File Every | Length of each video file | 2 minutes |
| File Name Prefix | Text at the start of each file name | e.g. the site name |
| IP Camera Direct Save | IP cameras only: saves the camera's own video without re-compressing it. Uses very little CPU (best for the Pi Zero), but resolution, frame rate and quality then come from the **camera's own settings** | On |

## Raspberry Pi: record automatically when it turns on

```bash
bash install_autostart.sh            # turn auto-start on
systemctl status nest-camera         # is it running?
journalctl -u nest-camera -f         # live log (Ctrl+C to leave)
sudo systemctl stop nest-camera      # stop for now (do this before opening the window)
bash install_autostart.sh --remove   # turn auto-start off
```

Set the camera and settings first with the window version (`python3 camera_ui.py --gui`,
then Settings -> Save). If the camera drops out, recording restarts by itself every few seconds.

Raspberry Pi packages needed (no pip):
```bash
sudo apt install python3-opencv python3-pil.imagetk python3-tk ffmpeg
```

## Troubleshooting

- **"Camera not found"**: check the camera is plugged in / has power, close other apps using it
  (Zoom, Teams, Meet), try another camera number, or check the IP camera link and password.
- **Picture too blurry**: open Settings and choose a higher Quality or Resolution
  (with IP Camera Direct Save on, change the camera's own settings instead).
- **Window doesn't open on the Pi**: you are probably connected with SSH. Use VNC, or record
  without a window.

## Files

| File | What it is |
|---|---|
| `camera_ui.py` | The app (window + start-up choice) |
| `camera_core.py` | Recording logic (no window needed) |
| `camera_headless.py` | Recording without a window |
| `install_autostart.sh` | Auto-start on the Raspberry Pi |
| `compression_test.py` | Compares file sizes of different settings using any video |
| `camera_settings.json` | Saved settings (created automatically, not uploaded to GitHub) |

## Known limits

- With IP Camera Direct Save, file size depends on the camera's own settings. A camera
  sending 25 fps at full quality makes about **800 MB per hour**, so lower the frame rate
  and bit rate in the camera's own settings page.
- The Pi Zero has no battery-backed clock. Without internet, the time can be wrong after a
  power cut, so file names and the date/time on the video may be off until it is synced.
- The IP camera link (including its password) is saved in `camera_settings.json` on the device.
