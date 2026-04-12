import collections
import threading
import time

import cv2
import mediapipe as mp
import numpy as np
from playsound import playsound
import os

# Initialize MediaPipe
mp_face_detection = mp.solutions.face_detection
mp_hands = mp.solutions.hands
mp_drawing = mp.solutions.drawing_utils

# Initialize face detection
face_detection = mp_face_detection.FaceDetection(min_detection_confidence=0.5)

# Initialize hand detection
hands = mp_hands.Hands(min_detection_confidence=0.5, min_tracking_confidence=0.5)

# Open webcam
cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("Cannot open camera")
    exit()

alert_cooldown = 5.0  # seconds
last_alert = 0.0
alert_until = 0.0

finger_history = collections.deque(maxlen=180)
continuous_touch_start = None

TIP_IDS = [
    mp_hands.HandLandmark.INDEX_FINGER_TIP,
    mp_hands.HandLandmark.MIDDLE_FINGER_TIP,
    mp_hands.HandLandmark.RING_FINGER_TIP,
    mp_hands.HandLandmark.PINKY_TIP,
]

def load_alert_images():
    img_files = ['first.png', 'second.png', 'third.png']
    images = []
    for f in img_files:
        img = cv2.imread(f, cv2.IMREAD_UNCHANGED) # IMREAD_UNCHANGED to keep alpha channel
        if img is not None:
            # Resize for visibility if they are very small pixel art
            img = cv2.resize(img, (300, 300), interpolation=cv2.INTER_NEAREST)
            images.append(img)
    return images

alert_images = load_alert_images()

def overlay_image(background, overlay, x, y):
    h, w = overlay.shape[:2]
    if x + w > background.shape[1] or y + h > background.shape[0]:
        return background
    
    # Split channels
    if overlay.shape[2] == 4:
        overlay_img = overlay[:, :, :3]
        mask = overlay[:, :, 3:] / 255.0
        background[y:y+h, x:x+w] = (1.0 - mask) * background[y:y+h, x:x+w] + mask * overlay_img
    else:
        background[y:y+h, x:x+w] = overlay
    return background

def play_alert():
    try:
        os.system('afplay alert.mov')
    except Exception as e:
        print(f"Audio error: {e}")


def threaded_alert():
    threading.Thread(target=play_alert, daemon=True).start()


def draw_alert_overlay(frame, text):
    overlay = frame.copy()
    cv2.rectangle(overlay, (20, 20), (620, 100), (0, 0, 255), -1)
    alpha = 0.7
    cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)
    cv2.putText(frame, text, (30, 75), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)


def is_active_back_and_forth(history):
    if len(history) < 16:
        return False

    xs = np.array([p[1] for p in history], dtype=np.float32)
    ys = np.array([p[2] for p in history], dtype=np.float32)

    if np.ptp(xs) < 80 and np.ptp(ys) < 80:
        return False

    dx = np.diff(xs)
    dy = np.diff(ys)
    dx_mask = np.abs(dx) > 10
    dy_mask = np.abs(dy) > 10
    x_dir = np.sign(dx) * dx_mask
    y_dir = np.sign(dy) * dy_mask

    x_changes = np.sum((x_dir[1:] != x_dir[:-1]) & (x_dir[1:] != 0) & (x_dir[:-1] != 0))
    y_changes = np.sum((y_dir[1:] != y_dir[:-1]) & (y_dir[1:] != 0) & (y_dir[:-1] != 0))

    return (x_changes >= 3 or y_changes >= 3) and (
        np.mean(np.abs(dx[dx_mask])) > 12 or np.mean(np.abs(dy[dy_mask])) > 12
    )


while True:
    ret, frame = cap.read()
    if not ret:
        break

    frame = cv2.flip(frame, 1)
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    face_results = face_detection.process(rgb_frame)
    hand_results = hands.process(rgb_frame)

    face_box = None
    if face_results.detections:
        detection = face_results.detections[0]
        bbox = detection.location_data.relative_bounding_box
        ih, iw, _ = frame.shape
        x, y, w, h = int(bbox.xmin * iw), int(bbox.ymin * ih), int(bbox.width * iw), int(bbox.height * ih)
        face_box = (x, y, w, h)
        cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)

    finger_positions = []
    touching_face = False

    if hand_results.multi_hand_landmarks and face_box is not None:
        fx, fy, fw, fh = face_box
        for hand_landmarks in hand_results.multi_hand_landmarks:
            mp_drawing.draw_landmarks(frame, hand_landmarks, mp_hands.HAND_CONNECTIONS)
            for tip_id in TIP_IDS:
                tip = hand_landmarks.landmark[tip_id]
                ih, iw, _ = frame.shape
                tip_x, tip_y = int(tip.x * iw), int(tip.y * ih)
                finger_positions.append((tip_x, tip_y))

                inside_face = fx <= tip_x <= fx + fw and fy <= tip_y <= fy + fh
                if inside_face:
                    touching_face = True
                    cv2.circle(frame, (tip_x, tip_y), 8, (0, 0, 255), -1)
                else:
                    cv2.circle(frame, (tip_x, tip_y), 5, (255, 255, 0), -1)

    current_time = time.time()
    if touching_face:
        if continuous_touch_start is None:
            continuous_touch_start = current_time
    else:
        continuous_touch_start = None

    if finger_positions and touching_face:
        avg_x = int(np.mean([p[0] for p in finger_positions]))
        avg_y = int(np.mean([p[1] for p in finger_positions]))
        finger_history.append((current_time, avg_x, avg_y))
    else:
        finger_history.clear()

    recent_history = [entry for entry in finger_history if current_time - entry[0] <= 6.0]
    active_motion = is_active_back_and_forth(recent_history)

    touch_duration = 0.0
    if continuous_touch_start is not None:
        touch_duration = current_time - continuous_touch_start

    if touch_duration >= 3.0 and active_motion:
        if current_time - last_alert > alert_cooldown:
            last_alert = current_time
            alert_until = current_time + 3.0
            threaded_alert()

    if current_time < alert_until and alert_images:
        img_idx = int((current_time * 0.7) % len(alert_images))
        frame = overlay_image(frame, alert_images[img_idx], 900, 400)

    if face_box is not None:
        cv2.putText(frame, f"Touch: {int(touch_duration)}s", (30, 140), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)
        cv2.putText(frame, f"Motion: {'yes' if active_motion else 'no'}", (30, 170), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)

    cv2.imshow('Face Picking Detection', frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
