import streamlit as st
import collections
import threading
import time
import cv2
import mediapipe as mp
import numpy as np
import os

# --- Page Configuration ---
st.set_page_config(page_title="Skin Health Monitor", page_icon="🛡️", layout="wide")

# --- Custom CSS for Styling ---
st.markdown("""
    <style>
    .main {
        background-color: #f5f7f9;
    }
    .stMetric {
        background-color: #ffffff;
        padding: 15px;
        border-radius: 10px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    </style>
    """, unsafe_allow_index=True)

# --- Initialization & Caching ---
@st.cache_resource
def load_mediapipe():
    mp_face = mp.solutions.face_detection.FaceDetection(min_detection_confidence=0.5)
    mp_hands = mp.solutions.hands.Hands(min_detection_confidence=0.5, min_tracking_confidence=0.5)
    return mp_face, mp_hands, mp.solutions.drawing_utils

face_detection, hands, mp_drawing = load_mediapipe()

@st.cache_resource
def load_assets():
    # Attempt to load overlay images if they exist
    images = []
    for f in ['first.png', 'second.png', 'third.png']:
        if os.path.exists(f):
            img = cv2.imread(f, cv2.IMREAD_UNCHANGED)
            if img is not None:
                img = cv2.resize(img, (300, 300), interpolation=cv2.INTER_NEAREST)
                images.append(img)
    return images

alert_images = load_assets()

# --- Utility Functions ---
def play_alert():
    try:
        # Note: afplay is macOS specific. 
        os.system('afplay alert.mov')
    except:
        pass

def is_active_motion(history):
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

# --- App UI Layout ---
st.title("🛡️ Face Picking Detection AI")

# App Description Section
if 'running' not in st.session_state:
    st.session_state.running = False

if not st.session_state.running:
    st.markdown("""
    ### Break the habit with real-time monitoring.
    This application uses your webcam to detect when you are touching your face. 
    It is specifically designed to help reduce **compulsive skin picking (dermatillomania)** by identifying repetitive motions.
    
    **How it works:**
    1. **Face & Hand Tracking:** Uses AI to identify the boundaries of your face and your fingertips.
    2. **Duration Check:** It ignores brief touches but tracks if your hand stays on your face for more than 3 seconds.
    3. **Motion Analysis:** It triggers an alert if it detects the "back-and-forth" motion associated with picking.
    4. **Alerts:** A visual overlay and audio cue will trigger to remind you to move your hand away.
    """)
    
    if st.button("🚀 Start Live Monitor", type="primary", use_container_width=True):
        st.session_state.running = True
        st.rerun()

else:
    # Monitor Mode UI
    if st.button("🛑 Stop Monitoring", type="secondary"):
        st.session_state.running = False
        st.rerun()

    col_vid, col_stats = st.columns([3, 1])
    
    video_placeholder = col_vid.empty()
    
    with col_stats:
        st.subheader("Live Stats")
        touch_metric = st.empty()
        motion_metric = st.empty()
        timer_metric = st.empty()

    # --- Core Logic Loop ---
    cap = cv2.VideoCapture(0)
    finger_history = collections.deque(maxlen=180)
    continuous_touch_start = None
    last_alert = 0.0
    alert_until = 0.0

    while st.session_state.running:
        ret, frame = cap.read()
        if not ret:
            st.error("Cannot access webcam.")
            break

        frame = cv2.flip(frame, 1)
        ih, iw, _ = frame.shape
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        face_results = face_detection.process(rgb_frame)
        hand_results = hands.process(rgb_frame)

        face_box = None
        if face_results.detections:
            bbox = face_results.detections[0].location_data.relative_bounding_box
            face_box = (int(bbox.xmin * iw), int(bbox.ymin * ih), int(bbox.width * iw), int(bbox.height * ih))
            cv2.rectangle(frame, (face_box[0], face_box[1]), 
                          (face_box[0]+face_box[2], face_box[1]+face_box[3]), (0, 255, 0), 2)

        touching_face = False
        finger_positions = []
        current_time = time.time()

        if hand_results.multi_hand_landmarks and face_box:
            fx, fy, fw, fh = face_box
            for hand_lms in hand_results.multi_hand_landmarks:
                mp_drawing.draw_landmarks(frame, hand_lms, mp.solutions.hands.HAND_CONNECTIONS)
                for tid in [8, 12, 16, 20]: # Index, Middle, Ring, Pinky tips
                    tip = hand_lms.landmark[tid]
                    tx, ty = int(tip.x * iw), int(tip.y * ih)
                    finger_positions.append((tx, ty))
                    if fx <= tx <= fx + fw and fy <= ty <= fy + fh:
                        touching_face = True
                        cv2.circle(frame, (tx, ty), 8, (0, 0, 255), -1)

        # Logic tracking
        if touching_face:
            if continuous_touch_start is None: continuous_touch_start = current_time
        else:
            continuous_touch_start = None

        if finger_positions and touching_face:
            avg_x = int(np.mean([p[0] for p in finger_positions]))
            avg_y = int(np.mean([p[1] for p in finger_positions]))
            finger_history.append((current_time, avg_x, avg_y))
        else:
            finger_history.clear()

        # Calculation
        active_motion = is_active_motion([e for e in finger_history if current_time - e[0] <= 6.0])
        touch_duration = current_time - continuous_touch_start if continuous_touch_start else 0.0

        # Alert Triggers
        if touch_duration >= 3.0 and active_motion:
            if current_time - last_alert > 5.0:
                last_alert = current_time
                alert_until = current_time + 3.0
                threading.Thread(target=play_alert, daemon=True).start()

        # Metrics Update
        touch_metric.metric("Hand on Face", "YES" if touching_face else "NO")
        motion_metric.metric("Picking Motion", "DETECTED" if active_motion else "NONE")
        timer_metric.metric("Continuous Touch", f"{touch_duration:.1f}s")

        # Visual Overlays
        if current_time < alert_until and alert_images:
            # Drawing a simple red border for alert if images aren't found
            cv2.rectangle(frame, (0,0), (iw, ih), (0,0,255), 20)
            st.warning("⚠️ PLEASE STOP PICKING")

        video_placeholder.image(frame, channels="BGR", use_container_width=True)

    cap.release()
