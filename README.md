# Face Picking Detection

This project uses computer vision to detect when a person is picking at their face and intervenes to prevent skin damage.

## Requirements

- Python 3.8+
- macOS with camera access

## Installation

1. Install dependencies:
   ```
   pip3 install -r requirements.txt
   ```

2. (Optional) Add an alert sound file named `alert.wav` in the project directory for audio alerts.

## Usage

Run the script:
```
python3 main.py
```

The program will open a window showing the camera feed with face mesh and hand detection overlays. It alerts when fingers are detected touching the face and moving back and forth for 5+ seconds by displaying a red "STOP PICKING YOUR FACE!" message on the video feed, printing to console, and playing an alert sound (if available).

Press 'q' in the window to quit.

## How it works

- Uses MediaPipe for face mesh (detailed face landmarks) and hand tracking.
- Detects when at least 2 finger tips are within 15 pixels of face landmarks (actual contact).
- Monitors hand motion for up-down oscillations (at least 3 direction changes in vertical position).
- Alerts only if actual contact + active up-down picking motion for 5+ seconds to avoid false positives.
- Prints a message and displays "STOP PICKING YOUR FACE!" on the video feed, plus plays an alert sound (if available).

## Customization

- Adjust touching threshold (currently 10 pixels) in `main.py`.
- Modify oscillation sensitivity (sign_changes >= 3).
- Change alert duration (5 seconds) or cooldown (5 seconds).