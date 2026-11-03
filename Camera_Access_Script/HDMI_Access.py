# HDMI access script (Laptop Version)
# Using OpenCV 

import cv2
import numpy as np

cap = cv2.VideoCapture(1)  # Capture video, change the index (inside the parentheses) to access different cameras

if not cap.isOpened():
    print("No camera :(")
    exit()
    
print('Press "q" in the video window to quit.')

while True:
  ret, frame = cap.read()

  if not ret:
    print('Failed to grab frame.')
    break

  # Display the live feed from the HDMI camera
  cv2.imshow('HDMI Camera Feed', frame)

  # Wait 1ms and check if 'q' was pressed to exit
  if cv2.waitKey(1) & 0xFF == ord('q'):
    break

# Release the capture device and close windows
cap.release()
cv2.destroyAllWindows()