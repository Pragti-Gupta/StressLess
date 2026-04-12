import streamlit as st
from streamlit_webrtc import webrtc_streamer, VideoTransformerBase
import collections
import time
import cv2
import mediapipe as mp
import numpy as np

# --- Page Config ---
st.set_page_config(page_title="Skin Health Monitor", page_icon="🛡️")

st.title("🛡️ Face Picking Detection AI")

# --- MediaPipe Setup (Global) ---
mp_face_detection = mp.solutions.face_detection
mp_hands = mp.solutions.hands
mp_drawing = mp.solutions.drawing_utils

class FacePickingTransformer(VideoTransformerBase):
    def __init__(self):
        self.face_detector = mp_face_detection.FaceDetection(min_detection_confidence=0.5)
        self.hands_detector = mp_hands.Hands(min_detection_confidence=0.5, min_tracking_confidence=0.5)
        self.finger_history = collections.deque(maxlen=180)
        self.continuous_touch_start = None
        self.last_alert_time = 0
        self.alert_active = False

    def is_active_motion(self, history):
        if len(history) < 16: return False
        xs = np.array([p[1] for p in history], dtype=np.float32)
        ys = np.array([p[2] for p in history], dtype=np.float32)
        if np.ptp(xs) < 80 and np.ptp(ys) < 80: return False
        dx, dy = np.diff(xs), np.diff(ys)
        dx_mask, dy_mask = np.abs(dx) > 10, np.abs(dy) > 10
        x_dir = np.sign(dx) * dx_mask
        y_dir = np.sign(dy) * dy_mask
        x_changes = np.sum((x_dir[1:] != x_dir[:-1]) & (x_dir[1:] != 0) & (x_dir[:-1] != 0))
        y_changes = np.sum((y_dir[1:] != y_dir[:-1]) & (y_dir[1:] != 0) & (y_dir[:-1] != 0))
        return (x_changes >= 3 or y_changes >= 3)

    def transform(self, frame):
        img = frame.to_ndarray(format="bgr24")
        img = cv2.flip(img, 1)
        ih, iw, _ = img.shape
        rgb_img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        face_results = self.face_detector.process(rgb_img)
        hand_results = self.hands_detector.process(rgb_img)

        face_box = None
        if face_results.detections:
            bbox = face_results.detections[0].location_data.relative_bounding_box
            face_box = (int(bbox.xmin * iw), int(bbox.ymin * ih), int(bbox.width * iw), int(bbox.height * ih))
            cv2.rectangle(img, (face_box[0], face_box[1]), 
                          (face_box[0]+face_box[2], face_box[1]+face_box[3]), (0, 255, 0), 2)

        touching_face = False
        current_time = time.time()
        finger_positions = []

        if hand_results.multi_hand_landmarks and face_box:
            fx, fy, fw, fh = face_box
            for hand_lms in hand_results.multi_hand_landmarks:
                mp_drawing.draw_landmarks(img, hand_lms, mp_hands.HAND_CONNECTIONS)
                for tid in [8, 12, 16, 20]:
                    tip = hand_lms.landmark[tid]
                    tx, ty = int(tip.x * iw), int(tip.y * ih)
                    finger_positions.append((tx, ty))
                    if fx <= tx <= fx + fw and fy <= ty <= fy + fh:
                        touching_face = True
                        cv2.circle(img, (tx, ty), 8, (0, 0, 255), -1)

        # Logic
        if touching_face:
            if self.continuous_touch_start is None: self.continuous_touch_start = current_time
        else:
            self.continuous_touch_start = None

        if finger_positions and touching_face:
            avg_x = int(np.mean([p[0] for p in finger_positions]))
            avg_y = int(np.mean([p[1] for p in finger_positions]))
            self.finger_history.append((current_time, avg_x, avg_y))
        
        touch_duration = current_time - self.continuous_touch_start if self.continuous_touch_start else 0.0
        active_motion = self.is_active_motion([e for e in self.finger_history if current_time - e[0] <= 6.0])

        # Visual Alert Overlay
        if touch_duration >= 3.0 and active_motion:
            self.alert_active = True
            cv2.rectangle(img, (0,0), (iw, ih), (0,0,255), 30)
            cv2.putText(img, "STOP PICKING!", (50, 100), cv2.FONT_HERSHEY_SIMPLEX, 2, (0,0,255), 5)
        else:
            self.alert_active = False

        return img

# --- UI Interface ---
st.markdown("""
### Instructions
1. Allow camera access in your browser.
2. Click **Start** to begin the live feed.
3. The AI will monitor for hands touching the face for >3 seconds with active motion.
""")

webrtc_streamer(
    key="face-picker",
    video_transformer_factory=FacePickingTransformer,
    rtc_configuration={"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]},
    media_stream_constraints={"video": True, "audio": False},
)
