"""
Find the RTSP video link of an IP camera.

Cheap IP cameras all use different link formats. This tries the most common ones
and tells you which one works, plus the camera's resolution and frame rate.

Usage (on the Pi or a laptop on the same network as the camera):
    python3 find_rtsp.py 192.168.1.192
    python3 find_rtsp.py 192.168.1.192 --user admin --password 123456

Then paste the link it finds into the Camera box of the app.
"""

import argparse
import re
import socket
import subprocess
import sys

from camera_core import find_ffmpeg, hide_password

# Common RTSP paths used by cheap / AliExpress / ONVIF cameras ({user} and {password}
# are filled in for brands that put them in the path, e.g. XMEye / Xiongmai).
PATHS = [
    "/stream1", "/stream0", "/live", "/live/ch00_0", "/live/ch00_1", "/live/main", "/live/0/MAIN",
    "/h264", "/h264Preview_01_main", "/11", "/12", "/0", "/1", "/av0_0", "/av0_1",
    "/onvif1", "/onvif/profile1/media.smp", "/media/video1", "/videoMain", "/video1",
    "/live.sdp", "/ch0_0.h264", "/1/h264major", "/mpeg4", "/unicast/c1/s0/live",
    "/Streaming/Channels/101", "/Streaming/Channels/1", "/h264/ch1/main/av_stream",
    "/cam/realmonitor?channel=1&subtype=0",
    "/user={user}&password={password}&channel=1&stream=0.sdp",
    "/user={user}_password={password}_channel=1_stream=0.sdp",
    "",
]
COMMON_PORTS = [554, 8554, 10554, 5544, 80, 8080]


def port_open(ip, port, timeout=2.0):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def try_url(ffmpeg, url, seconds=12):
    """Return a description of the video stream if the link works, else None."""
    cmd = [ffmpeg, "-hide_banner", "-rtsp_transport", "tcp", "-timeout", "5000000",
           "-i", url, "-frames:v", "1", "-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=seconds)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    m = re.search(r"Stream #\S+: Video: (.*)", r.stderr)
    return m.group(1).strip() if m else "video stream found"


def main():
    p = argparse.ArgumentParser(description="Find the RTSP link of an IP camera.")
    p.add_argument("ip", help="camera IP address, e.g. 192.168.1.192")
    p.add_argument("--user", default="admin")
    p.add_argument("--password", default="123456")
    p.add_argument("--port", type=int, help="RTSP port if you already know it (normally 554)")
    args = p.parse_args()

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        sys.exit("ffmpeg not found. On the Pi: sudo apt install ffmpeg")

    print(f"Checking {args.ip} ...")
    open_ports = [port for port in COMMON_PORTS if port_open(args.ip, port)]
    if not open_ports:
        print("\nCan't reach the camera at all. Check that:")
        print(" - the camera has power and its network cable is plugged in")
        print(f" - this computer is on the same network (its IP should start with "
              f"{'.'.join(args.ip.split('.')[:3])}.)")
        sys.exit(1)
    print("Open ports:", ", ".join(str(p) for p in open_ports))
    if 80 in open_ports or 8080 in open_ports:
        web = 80 if 80 in open_ports else 8080
        print(f"Web settings page: http://{args.ip}{'' if web == 80 else ':8080'}  "
              f"(log in with {args.user} / your password)")

    rtsp_ports = [args.port] if args.port else [p for p in open_ports if p not in (80, 8080)]
    if not rtsp_ports:
        print("\nNo RTSP port is open. Log in to the camera's web page and look for "
              "'RTSP' (often under Network settings) to turn it on or see its port.")
        sys.exit(1)

    for port in rtsp_ports:
        for path in PATHS:
            path = path.format(user=args.user, password=args.password)
            url = f"rtsp://{args.user}:{args.password}@{args.ip}:{port}{path}"
            print(f"  trying {hide_password(url)} ...", end=" ", flush=True)
            info = try_url(ffmpeg, url)
            if info:
                print("WORKS!")
                print(f"\nVideo: {info}")
                print("\nPaste this link into the Camera box of the app:")
                print(f"  {url}")
                return
            print("no")

    print("\nNone of the common links worked. Log in to the camera's web page and look for "
          "the RTSP address (often under Network -> RTSP), or check the camera's manual.")
    sys.exit(1)


if __name__ == "__main__":
    main()
