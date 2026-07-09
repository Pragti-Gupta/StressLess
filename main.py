import cv2
import mediapipe as mp
import numpy as np
import time
import os
import csv
import sys
from datetime import datetime
from random import choice, randint
from tkinter import messagebox
import tkinter as tk

# --- Config ---
SUGGESTIONS = [
    "Grab a fidget spinner or toy!",
    "Squeeze a stress ball 5 times",
    "Tap your desk or play with a pen",
    "Take 3 deep, slow breaths",
    "Stretch your shoulders and hands",
]
DATA_DIR = "data"
LOG_FILE = os.path.join(DATA_DIR, "picking_log.csv")
LABELS_FILE = os.path.join(DATA_DIR, "labels.csv")

# --- StressLess / Taiyaki Guard Color Palette ---
COLOR_BG_CREAM     = (240, 247, 254)
COLOR_TEXT_BROWN   = (43, 50, 74)   
COLOR_ACCENT_CORAL = (71, 114, 241) 
COLOR_MUTED_PEACH  = (211, 230, 253)
COLOR_WHITE        = (255, 255, 255)

# --- Interactive State Variables ---
tracking_enabled = True
camera_enabled = True
should_quit = False
alert_window_open = False

# --- Priority Zone Dropdown State Variables ---
ZONES = ["All Zones", "Lips/Mouth", "Nose Area", "Eyebrows"]
current_priority_idx = 0
dropdown_expanded = False

# --- Divert Attention States ---
current_suggestion = choice(SUGGESTIONS)
fidget_mode = False    
active_diversion = "menu"
fidget_start_time = None
lock_in_triggered = False

# --- Core Simulation Mechanics ---
spinner_angle = 0.0    
spinner_speed = 0.0    
is_dragging_spinner = False
bubble_sheet = []

# --- WINDOW HITBOXES (Main Window) ---
btn_tracking_box = [20, 20, 220, 120]
btn_camera_box   = [240, 20, 440, 120]
btn_quit_box     = [460, 20, 573, 120]

# NEW DESIGNED DROPDOWN: Positioned dynamically relative to the right edge (calculated inside loop)
# Width is 260px, Height is 55px for a much larger, luxurious interface button.
dropdown_width = 260
dropdown_height = 55
dropdown_margin_right = 20
dropdown_margin_top = 20

# --- HD POPUP WINDOW HITBOXES (1000x680 Canvas) ---
btn_divert_box = [250, 220, 750, 310]
btn_choose_spinner   = [80, 300, 920, 390]
btn_choose_bubbles   = [80, 420, 920, 510]
btn_choose_breathing = [80, 540, 920, 630]

# --- MediaPipe FaceMesh High-Risk Mapping Subsets ---
LIPS_INDICES = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291]
NOSE_TIP_INDEX = 1 
LEFT_EYEBROW = [70, 63, 105, 66, 107]
RIGHT_EYEBROW = [336, 296, 334, 293, 300]
TARGET_FACIAL_INDICES = LIPS_INDICES + [NOSE_TIP_INDEX] + LEFT_EYEBROW + RIGHT_EYEBROW

# --- Helpers ---
def ensure_data_dir():
    global DATA_DIR, LOG_FILE, LABELS_FILE
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(base_dir, "data")
    LOG_FILE = os.path.join(DATA_DIR, "picking_log.csv")
    LABELS_FILE = os.path.join(DATA_DIR, "labels.csv")
    try: os.makedirs(DATA_DIR, exist_ok=True)
    except Exception: pass

def log_event(timestamp_iso, user_id, picker_type, event_type, duration_s, motion):
    ensure_data_dir()
    header = ["timestamp", "user_id", "picker_type", "event", "duration_s", "motion"]
    row = [timestamp_iso, user_id, picker_type, event_type, f"{duration_s:.2f}", str(bool(motion))]
    write_header = not os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header: w.writerow(header)
        w.writerow(row)

def append_label(timestamp_iso, user_id, picker_type, label):
    ensure_data_dir()
    header = ["timestamp", "user_id", "picker_type", "label"]
    row = [timestamp_iso, user_id, picker_type, label]
    write_header = not os.path.exists(LABELS_FILE)
    with open(LABELS_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header: w.writerow(header)
        w.writerow(row)

def is_active_motion(history, current_time):
    recent = [e for e in history if current_time - e[0] <= 6.0]
    if len(recent) < 8: return False
    xs = np.array([p[1] for p in recent])
    ys = np.array([p[2] for p in recent])
    if np.ptp(xs) < 40 and np.ptp(ys) < 40: return False
    dx = np.diff(xs)
    x_dir = np.sign(dx) * (np.abs(dx) > 6)
    x_changes = np.sum((x_dir[1:] != x_dir[:-1]) & (x_dir[1:] != 0) & (x_dir[:-1] != 0))
    return x_changes >= 2

def generate_bubble_wrap_hd():
    bubbles = []
    rows, cols = 3, 4
    start_x, start_y = 170, 320
    spacing_x, spacing_y = 220, 130
    for r in range(rows):
        for c in range(cols):
            cx = start_x + (c * spacing_x)
            cy = start_y + (r * spacing_y)
            bubbles.append({"x": cx, "y": cy, "r": 45, "popped": False, "burst_t": 0.0})
    return bubbles

# --- Dynamic Dropdown Hitbox Resolver ---
def get_dropdown_coords(frame_width):
    x1 = frame_width - dropdown_margin_right - dropdown_width
    y1 = dropdown_margin_top
    x2 = frame_width - dropdown_margin_right
    y2 = dropdown_margin_top + dropdown_height
    return [x1, y1, x2, y2]

# --- Mouse Handlers ---
def mouse_callback_main(event, x, y, flags, param):
    global tracking_enabled, camera_enabled, should_quit, dropdown_expanded, current_priority_idx
    
    # We pass the active window frame shape width through param to track coordinates dynamically
    frame_w = param if isinstance(param, int) else 640
    dd_box = get_dropdown_coords(frame_w)
    
    if event == cv2.EVENT_LBUTTONDOWN:
        # 1. Handle Selection Clicks on Expanded Dropdown Items
        if dropdown_expanded:
            item_height = 45
            for i in range(len(ZONES)):
                item_y1 = dd_box[3] + (i * item_height)
                item_y2 = item_y1 + item_height
                if dd_box[0] <= x <= dd_box[2] and item_y1 <= y <= item_y2:
                    current_priority_idx = i
                    dropdown_expanded = False
                    return
            # Close dropdown if click occurs outside the selector region
            if not (dd_box[0] <= x <= dd_box[2] and dd_box[1] <= y <= dd_box[3]):
                dropdown_expanded = False
                return

        # 2. Main Dashboard Layout Hitboxes
        if btn_tracking_box[0] <= x <= btn_tracking_box[2] and btn_tracking_box[1] <= y <= btn_tracking_box[3]:
            tracking_enabled = not tracking_enabled
        elif btn_camera_box[0] <= x <= btn_camera_box[2] and btn_camera_box[1] <= y <= btn_camera_box[3]:
            camera_enabled = not camera_enabled
        elif btn_quit_box[0] <= x <= btn_quit_box[2] and btn_quit_box[1] <= y <= btn_quit_box[3]:
            should_quit = True
        elif dd_box[0] <= x <= dd_box[2] and dd_box[1] <= y <= dd_box[3]:
            dropdown_expanded = not dropdown_expanded

def mouse_callback_popup(event, x, y, flags, param):
    global fidget_mode, active_diversion, fidget_start_time, lock_in_triggered
    global spinner_speed, spinner_angle, is_dragging_spinner, bubble_sheet

    cx, cy = 500, 450

    if event == cv2.EVENT_LBUTTONDOWN:
        if not fidget_mode and btn_divert_box[0] <= x <= btn_divert_box[2] and btn_divert_box[1] <= y <= btn_divert_box[3]:
            fidget_mode = True
            active_diversion = "menu"
            lock_in_triggered = False
            return

        if fidget_mode and active_diversion == "menu":
            if btn_choose_spinner[0] <= x <= btn_choose_spinner[2] and btn_choose_spinner[1] <= y <= btn_choose_spinner[3]:
                active_diversion = "spinner"
                fidget_start_time = time.time()
                spinner_speed = 12.0
            elif btn_choose_bubbles[0] <= x <= btn_choose_bubbles[2] and btn_choose_bubbles[1] <= y <= btn_choose_bubbles[3]:
                active_diversion = "bubbles"
                fidget_start_time = time.time()
                bubble_sheet = generate_bubble_wrap_hd()
            elif btn_choose_breathing[0] <= x <= btn_choose_breathing[2] and btn_choose_breathing[1] <= y <= btn_choose_breathing[3]:
                active_diversion = "breathing"
                fidget_start_time = time.time()
            return

        if fidget_mode and active_diversion == "spinner" and not lock_in_triggered:
            dist = np.hypot(x - cx, y - cy)
            if dist < 180:
                is_dragging_spinner = True
                mouse_angle_rad = np.arctan2(y - cy, x - cx)
                spinner_angle = mouse_angle_rad * 180.0 / np.pi

        if fidget_mode and active_diversion == "bubbles" and not lock_in_triggered:
            for bubble in bubble_sheet:
                if not bubble["popped"]:
                    if np.hypot(x - bubble["x"], y - bubble["y"]) < bubble["r"]:
                        bubble["popped"] = True
                        bubble["burst_t"] = time.time()
                        break

    elif event == cv2.EVENT_MOUSEMOVE:
        if active_diversion == "spinner" and is_dragging_spinner and not lock_in_triggered:
            mouse_angle_rad = np.arctan2(y - cy, x - cx)
            new_angle = mouse_angle_rad * 180.0 / np.pi
            diff = new_angle - spinner_angle
            if diff > 180: diff -= 360
            elif diff < -180: diff += 360
            spinner_speed = np.clip(diff * 0.8, -45, 45)
            spinner_angle = new_angle

    elif event == cv2.EVENT_LBUTTONUP:
        is_dragging_spinner = False

def draw_fidget_spinner_hd(canvas, cx, cy, angle_deg):
    rad = angle_deg * np.pi / 180.0
    for i in range(3):
        branch_angle = rad + i * (2 * np.pi / 3)
        bx = int(cx + 110 * np.cos(branch_angle))
        by = int(cy + 110 * np.sin(branch_angle))
        cv2.circle(canvas, (bx, by), 50, COLOR_ACCENT_CORAL, -1, cv2.LINE_AA)
        cv2.circle(canvas, (bx, by), 24, COLOR_TEXT_BROWN, -1, cv2.LINE_AA)
        cv2.circle(canvas, (bx, by), 12, COLOR_BG_CREAM, -1, cv2.LINE_AA)
        cv2.line(canvas, (cx, cy), (bx, by), COLOR_ACCENT_CORAL, 30, cv2.LINE_AA)
    cv2.circle(canvas, (cx, cy), 40, COLOR_MUTED_PEACH, -1, cv2.LINE_AA)
    cv2.circle(canvas, (cx, cy), 32, COLOR_TEXT_BROWN, 4, cv2.LINE_AA)
    cv2.circle(canvas, (cx, cy), 12, COLOR_WHITE, -1, cv2.LINE_AA)

def draw_taiyaki_fish_hd(canvas, cx, cy, scale, img_mascot=None):
    if img_mascot is not None:
        try:
            orig_h, orig_w = img_mascot.shape[:2]
            base_max_dim = 280
            
            if orig_w > orig_h:
                target_w = int(base_max_dim * scale)
                target_h = int((orig_h / orig_w) * target_w)
            else:
                target_h = int(base_max_dim * scale)
                target_w = int((orig_w / orig_h) * target_h)
            
            resized = cv2.resize(img_mascot, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
            
            x1 = cx - (target_w // 2)
            y1 = cy - (target_h // 2)
            x2 = x1 + target_w
            y2 = y1 + target_h
            
            canvas_h, canvas_w = canvas.shape[:2]
            if x1 >= 0 and y1 >= 0 and x2 <= canvas_w and y2 <= canvas_h:
                if resized.shape[2] == 4:
                    alpha = resized[:, :, 3] / 255.0
                    alpha = np.expand_dims(alpha, axis=2)
                    img_rgb = resized[:, :, :3]
                    roi = canvas[y1:y2, x1:x2]
                    blended = (img_rgb * alpha + roi * (1 - alpha)).astype(np.uint8)
                    canvas[y1:y2, x1:x2] = blended
                else:
                    canvas[y1:y2, x1:x2] = resized
                return
        except Exception:
            pass

    base_color = COLOR_ACCENT_CORAL
    cv2.ellipse(canvas, (cx, cy), (int(140 * scale), int(96 * scale)), 0, 0, 360, base_color, -1, cv2.LINE_AA)
    pts_tail = np.array([
        [cx - int(130 * scale), cy], [cx - int(220 * scale), cy - int(70 * scale)], [cx - int(220 * scale), cy + int(70 * scale)]
    ], np.int32)
    cv2.fillPoly(canvas, [pts_tail], base_color, cv2.LINE_AA)
    cv2.circle(canvas, (cx + int(80 * scale), cy - int(24 * scale)), int(12 * scale), COLOR_WHITE, -1, cv2.LINE_AA)
    cv2.circle(canvas, (cx + int(84 * scale), cy - int(24 * scale)), int(6 * scale), COLOR_TEXT_BROWN, -1, cv2.LINE_AA)
    cv2.ellipse(canvas, (cx + int(20 * scale), cy), (int(16 * scale), int(44 * scale)), 0, 0, 180, COLOR_MUTED_PEACH, 6, cv2.LINE_AA)

def draw_image_button(frame, box, img):
    if img is None: return False
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    try:
        resized = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
        if resized.shape[2] == 4:
            alpha = resized[:, :, 3] / 255.0
            alpha = np.expand_dims(alpha, axis=2)
            img_rgb = resized[:, :, :3]
            roi = frame[y1:y2, x1:x2]
            blended = (img_rgb * alpha + roi * (1 - alpha)).astype(np.uint8)
            frame[y1:y2, x1:x2] = blended
        else:
            frame[y1:y2, x1:x2] = resized
        return True
    except Exception:
        return False

# --- Main Application Loop ---
def run(user_id="anonymous", picker_type="skin picking"):
    global tracking_enabled, camera_enabled, should_quit, alert_window_open
    global current_suggestion, fidget_mode, active_diversion, fidget_start_time, lock_in_triggered
    global spinner_angle, spinner_speed, is_dragging_spinner, bubble_sheet
    global dropdown_expanded, current_priority_idx
    
    ensure_data_dir()
    root = tk.Tk()
    root.withdraw()
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        messagebox.showerror("Camera Error", "Taiyaki Guard could not access your webcam.")
        return

    # Load Assets
    img_video_on = cv2.imread("videoon.png", -1)
    img_video_off = cv2.imread("videooff.png", -1)
    img_tracking_on = cv2.imread("trackingon.png", -1)
    img_tracking_off = cv2.imread("trackingoff.png", -1)
    img_quit = cv2.imread("quit.png", -1)
    img_taiyaki_mascot = cv2.imread("taiyaki.png", -1)

    # Initialize Face Mesh & Hands Frameworks
    mp_face_mesh = mp.solutions.face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5
    )
    mp_hands = mp.solutions.hands.Hands(
        max_num_hands=2,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5
    )
    TIP_IDS = [4, 8, 12, 16, 20]

    finger_history = []
    continuous_touch_start = None
    alert_until = 0
    last_event_ts = None

    cv2.namedWindow('Taiyaki Guard')

    while True:
        if should_quit or cv2.getWindowProperty('Taiyaki Guard', cv2.WND_PROP_VISIBLE) < 1:
            break

        ret, frame = cap.read()
        if not ret:
            cv2.waitKey(10)
            time.sleep(0.05)
            continue
        
        frame = cv2.flip(frame, 1)
        h, w = frame.shape[:2]
        current_time = time.time()

        # Update width parameter mapping for the click callback
        cv2.setMouseCallback('Taiyaki Guard', mouse_callback_main, param=w)

        if not camera_enabled:
            frame = np.zeros((h, w, 3), dtype=np.uint8)
            frame[:] = COLOR_BG_CREAM
            cv2.putText(frame, "Monitoring Paused", (int(w/2) - 140, int(h/2)),
                        cv2.FONT_HERSHEY_DUPLEX, 0.8, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)
        
        touching = False
        motion = False
        duration = 0.0

        if camera_enabled and tracking_enabled:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            face_res = mp_face_mesh.process(rgb)
            hand_res = mp_hands.process(rgb)

            face_landmarks_dict = {}
            all_face_points = []
           
            if face_res.multi_face_landmarks:
                for face_landmarks in face_res.multi_face_landmarks:
                    for idx, lm in enumerate(face_landmarks.landmark):
                        all_face_points.append((lm.x, lm.y, lm.z))
                       
                        if idx in TARGET_FACIAL_INDICES:
                            face_landmarks_dict[idx] = (lm.x, lm.y, lm.z, int(lm.x * w), int(lm.y * h))
                            
                            is_highlighted_target = False
                            if current_priority_idx == 1 and idx in LIPS_INDICES:
                                is_highlighted_target = True
                            elif current_priority_idx == 2 and idx == NOSE_TIP_INDEX:
                                is_highlighted_target = True
                            elif current_priority_idx == 3 and (idx in LEFT_EYEBROW or idx in RIGHT_EYEBROW):
                                is_highlighted_target = True

                            dot_color = COLOR_ACCENT_CORAL if is_highlighted_target else COLOR_MUTED_PEACH
                            dot_radius = 4 if is_highlighted_target else 2
                            cv2.circle(frame, (int(lm.x * w), int(lm.y * h)), dot_radius, dot_color, -1)

            finger_pos = []
            hand_landmarks_list = []
            if hand_res.multi_hand_landmarks:
                for hand in hand_res.multi_hand_landmarks:
                    for tid in TIP_IDS:
                        lm = hand.landmark[tid]
                        tx, ty = int(lm.x * w), int(lm.y * h)
                        finger_pos.append((tx, ty))
                        hand_landmarks_list.append(lm)
                        cv2.circle(frame, (tx, ty), 4, COLOR_MUTED_PEACH, -1)

            if hand_landmarks_list and all_face_points:
                for h_lm in hand_landmarks_list:
                    for f_point in all_face_points[::12]: 
                        gen_dist_3d = np.sqrt((h_lm.x - f_point[0])**2 + (h_lm.y - f_point[1])**2 + (h_lm.z - f_point[2])**2)
                        if gen_dist_3d < 0.11: 
                            touching = True
                   
                    for idx, (fx_norm, fy_norm, fz_norm, fx_px, fy_px) in face_landmarks_dict.items():
                        hotspot_dist_3d = np.sqrt((h_lm.x - fx_norm)**2 + (h_lm.y - fy_norm)**2 + (h_lm.z - fz_norm)**2)
                       
                        adaptive_threshold = 0.085
                        if current_priority_idx == 1 and idx in LIPS_INDICES:
                            adaptive_threshold = 0.115
                        elif current_priority_idx == 2 and idx == NOSE_TIP_INDEX:
                            adaptive_threshold = 0.115
                        elif current_priority_idx == 3 and (idx in LEFT_EYEBROW or idx in RIGHT_EYEBROW):
                            adaptive_threshold = 0.115

                        if hotspot_dist_3d < adaptive_threshold:
                            touching = True
                            cv2.circle(frame, (fx_px, fy_px), 6, COLOR_ACCENT_CORAL, -1)

            if touching:
                if continuous_touch_start is None:
                    continuous_touch_start = current_time
                if finger_pos:
                    avg_x = int(np.mean([p[0] for p in finger_pos]))
                    avg_y = int(np.mean([p[1] for p in finger_pos]))
                    finger_history.append((current_time, avg_x, avg_y))
                if len(finger_history) > 180:
                    finger_history = finger_history[-180:]
            else:
                continuous_touch_start = None
                finger_history = []

            motion = is_active_motion(finger_history, current_time)
            duration = current_time - continuous_touch_start if continuous_touch_start else 0

            if duration >= 5.0 and motion and current_time >= alert_until:
                alert_until = current_time + 8.0
                last_event_ts = datetime.utcnow().isoformat()
                log_event(last_event_ts, user_id, picker_type, "alert", duration, motion)
                current_suggestion = choice(SUGGESTIONS)
                fidget_mode = False
                active_diversion = "menu"
                lock_in_triggered = False
        else:
            continuous_touch_start = None
            finger_history = []

        if fidget_mode and active_diversion != "menu" and fidget_start_time is not None:
            elapsed_fidget = current_time - fidget_start_time
            if elapsed_fidget < 18.0:
                alert_until = current_time + 1.0
        elif fidget_mode and active_diversion == "menu":
            alert_until = current_time + 1.0

        alerting = current_time < alert_until

        # --- Popup Intervention Window ---
        if alerting and tracking_enabled:
            alert_w, alert_h = 1000, 680
            alert_frame = np.zeros((alert_h, alert_w, 3), dtype=np.uint8)
            alert_frame[:] = COLOR_BG_CREAM
        
            cv2.rectangle(alert_frame, (0, 0), (alert_w, 24), COLOR_ACCENT_CORAL, -1)
            cv2.rectangle(alert_frame, (0, 0), (alert_w, alert_h), COLOR_MUTED_PEACH, 6)
        
            cv2.putText(alert_frame, "TAIYAKI INTERVENTION:", (50, 90), cv2.FONT_HERSHEY_DUPLEX, 1.1, COLOR_ACCENT_CORAL, 3, cv2.LINE_AA)
            cv2.putText(alert_frame, f'"{current_suggestion}"', (50, 160), cv2.FONT_HERSHEY_DUPLEX, 0.9, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)
        
            if not fidget_mode:
                cv2.rectangle(alert_frame, (btn_divert_box[0], btn_divert_box[1]), (btn_divert_box[2], btn_divert_box[3]), COLOR_ACCENT_CORAL, -1, cv2.LINE_AA)
                cv2.putText(alert_frame, "Divert Attention", (btn_divert_box[0] + 110, btn_divert_box[1] + 58), cv2.FONT_HERSHEY_SIMPLEX, 1.0, COLOR_WHITE, 3, cv2.LINE_AA)
          
            elif active_diversion == "menu":
                cv2.putText(alert_frame, "CHOOSE YOUR DIVERSION TYPE:", (50, 240), cv2.FONT_HERSHEY_DUPLEX, 0.8, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)
              
                for btn, name in [(btn_choose_spinner, "1. Kinetic Fidget Spinner"),
                                  (btn_choose_bubbles, "2. Pop Bubble Wrap (HD Grid)"),
                                  (btn_choose_breathing, "3. Taiyaki Breathing Guide (Synced)")]:
                    cv2.rectangle(alert_frame, (btn[0], btn[1]), (btn[2], btn[3]), COLOR_ACCENT_CORAL, -1, cv2.LINE_AA)
                    cv2.putText(alert_frame, name, (btn[0] + 40, btn[1] + 55), cv2.FONT_HERSHEY_SIMPLEX, 1.0, COLOR_WHITE, 3, cv2.LINE_AA)

            else:
                time_left = 15 - int(current_time - fidget_start_time)
              
                if time_left > 0:
                    cv2.putText(alert_frame, f"Time left: {time_left}s", (750, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.0, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)
                  
                    if active_diversion == "spinner":
                        if not is_dragging_spinner:
                            spinner_angle += spinner_speed
                            spinner_speed *= 0.975
                            if abs(spinner_speed) < 0.05: spinner_speed = 0.0
                        cv2.putText(alert_frame, "Click & Drag on the Spinner to SPIN it!", (230, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)
                        draw_fidget_spinner_hd(alert_frame, 500, 450, spinner_angle)
                  
                    elif active_diversion == "bubbles":
                        text_size = cv2.getTextSize("Click the sheet bubbles to pop them!", cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0]
                        cv2.putText(alert_frame, "Click the sheet bubbles to pop them!", (int(500 - text_size[0]/2), 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)
                        for b in bubble_sheet:
                            if not b["popped"]:
                                cv2.circle(alert_frame, (b["x"], b["y"]), b["r"], COLOR_MUTED_PEACH, -1, cv2.LINE_AA)
                                cv2.circle(alert_frame, (b["x"], b["y"]), b["r"] - 4, COLOR_WHITE, 2, cv2.LINE_AA)
                                cv2.circle(alert_frame, (b["x"] - 12, b["y"] - 12), 8, COLOR_WHITE, -1, cv2.LINE_AA)
                            else:
                                cv2.circle(alert_frame, (b["x"], b["y"]), b["r"], COLOR_TEXT_BROWN, 2, cv2.LINE_AA)
                                if current_time - b["burst_t"] < 0.25:
                                    cv2.circle(alert_frame, (b["x"], b["y"]), b["r"] + 12, COLOR_ACCENT_CORAL, 3, cv2.LINE_AA)
                  
                    elif active_diversion == "breathing":
                        running_delta = current_time - fidget_start_time
                        wave_sin = np.sin(running_delta * (2 * np.pi / 8.0))
                        wave_cos = np.cos(running_delta * (2 * np.pi / 8.0))
                      
                        scale_factor = 0.85 + 0.45 * wave_sin
                        breathe_label = "BREATHE IN..." if wave_cos >= 0 else "BREATHE OUT..."
                      
                        label_x = 360 if wave_cos >= 0 else 345
                        cv2.putText(alert_frame, breathe_label, (label_x, 240), cv2.FONT_HERSHEY_DUPLEX, 1.1, COLOR_TEXT_BROWN, 3, cv2.LINE_AA)
                      
                        cv2.circle(alert_frame, (500, 450), int(210 * scale_factor), COLOR_MUTED_PEACH, 4, cv2.LINE_AA)
                        draw_taiyaki_fish_hd(alert_frame, 500, 450, scale_factor, img_mascot=img_taiyaki_mascot)
              
                else:
                    lock_in_triggered = True
                    cv2.rectangle(alert_frame, (100, 260), (900, 520), COLOR_MUTED_PEACH, -1, cv2.LINE_AA)
                    cv2.rectangle(alert_frame, (100, 260), (900, 520), COLOR_ACCENT_CORAL, 4, cv2.LINE_AA)
                    cv2.putText(alert_frame, "TIME TO LOCK IN!", (285, 360), cv2.FONT_HERSHEY_DUPLEX, 1.5, COLOR_TEXT_BROWN, 4, cv2.LINE_AA)
                    cv2.putText(alert_frame, "Hands off face, you got this.", (265, 440), cv2.FONT_HERSHEY_SIMPLEX, 1.0, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)

            cv2.imshow('Taiyaki Intervention', alert_frame)
        
            if not alert_window_open:
                cv2.setWindowProperty('Taiyaki Intervention', cv2.WND_PROP_TOPMOST, 1)
                cv2.setMouseCallback('Taiyaki Intervention', mouse_callback_popup)
                alert_window_open = True
        else:
            if alert_window_open:
                cv2.destroyWindow('Taiyaki Intervention')
                alert_window_open = False
                fidget_mode = False
                active_diversion = "menu"
                lock_in_triggered = False

        # --- Main Window Dashboard HUD ---
        active_tracking_img = img_tracking_on if tracking_enabled else img_tracking_off
        if not draw_image_button(frame, btn_tracking_box, active_tracking_img):
            t_bg = COLOR_ACCENT_CORAL if tracking_enabled else COLOR_MUTED_PEACH
            t_fg = COLOR_WHITE if tracking_enabled else COLOR_TEXT_BROWN
            t_text = "Track: ON" if tracking_enabled else "Track: OFF"
            cv2.rectangle(frame, (btn_tracking_box[0], btn_tracking_box[1]), (btn_tracking_box[2], btn_tracking_box[3]), t_bg, -1, cv2.LINE_AA)
            cv2.putText(frame, t_text, (btn_tracking_box[0] + 10, btn_tracking_box[1] + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.4, t_fg, 1, cv2.LINE_AA)

        active_video_img = img_video_on if camera_enabled else img_video_off
        if not draw_image_button(frame, btn_camera_box, active_video_img):
            c_bg = COLOR_ACCENT_CORAL if camera_enabled else COLOR_MUTED_PEACH
            c_fg = COLOR_WHITE if camera_enabled else COLOR_TEXT_BROWN
            c_text = "Cam: ON" if camera_enabled else "Cam: OFF"
            cv2.rectangle(frame, (btn_camera_box[0], btn_camera_box[1]), (btn_camera_box[2], btn_camera_box[3]), c_bg, -1, cv2.LINE_AA)
            cv2.putText(frame, c_text, (btn_camera_box[0] + 10, btn_camera_box[1] + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.4, c_fg, 1, cv2.LINE_AA)

        if not draw_image_button(frame, btn_quit_box, img_quit):
            cv2.rectangle(frame, (btn_quit_box[0], btn_quit_box[1]), (btn_quit_box[2], btn_quit_box[3]), COLOR_TEXT_BROWN, -1, cv2.LINE_AA)
            cv2.putText(frame, "X", (btn_quit_box[0] + 18, btn_quit_box[1] + 32), cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_WHITE, 2, cv2.LINE_AA)

        # --- THEMED TOP-RIGHT CORNER LARGE DROPDOWN OVERLAY ---
        dd_box = get_dropdown_coords(w)
        
        # Base container button filled with smooth theme matching white and outlined cleanly in coral accent
        cv2.rectangle(frame, (dd_box[0], dd_box[1]), (dd_box[2], dd_box[3]), COLOR_WHITE, -1, cv2.LINE_AA)
        cv2.rectangle(frame, (dd_box[0], dd_box[1]), (dd_box[2], dd_box[3]), COLOR_ACCENT_CORAL, 2, cv2.LINE_AA)
        
        # Expanded bold text sizing matching theme fonts
        cv2.putText(frame, f"Watch: {ZONES[current_priority_idx]}", (dd_box[0] + 15, dd_box[1] + 34), 
                    cv2.FONT_HERSHEY_DUPLEX, 0.55, COLOR_TEXT_BROWN, 1, cv2.LINE_AA)
        # Downward visual caret symbol indicator positioned near the corner
        cv2.putText(frame, "v", (dd_box[2] - 25, dd_box[1] + 32), cv2.FONT_HERSHEY_DUPLEX, 0.55, COLOR_TEXT_BROWN, 1, cv2.LINE_AA)

        if dropdown_expanded:
            item_height = 45
            for i, zone_name in enumerate(ZONES):
                item_y1 = dd_box[3] + (i * item_height)
                item_y2 = item_y1 + item_height
                
                # Alternate colors or highlight hovered zones elegantly to respect the theme
                bg_item_color = COLOR_MUTED_PEACH if i == current_priority_idx else COLOR_WHITE
                
                cv2.rectangle(frame, (dd_box[0], item_y1), (dd_box[2], item_y2), bg_item_color, -1, cv2.LINE_AA)
                cv2.rectangle(frame, (dd_box[0], item_y1), (dd_box[2], item_y2), COLOR_MUTED_PEACH, 1, cv2.LINE_AA)
                cv2.putText(frame, zone_name, (dd_box[0] + 20, item_y1 + 28), 
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, COLOR_TEXT_BROWN, 1, cv2.LINE_AA)

        # --- Info Bar ---
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, h - 75), (w, h), COLOR_BG_CREAM, -1)
        cv2.addWeighted(overlay, 0.85, frame, 0.15, 0, frame)
    
        cv2.line(frame, (0, h - 75), (w, h - 75), COLOR_MUTED_PEACH, 2, cv2.LINE_AA)
        cv2.putText(frame, f"Timer: {duration:.1f}s", (25, h - 32), cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)
    
        status_text = "Status: Suspended"
        status_color = COLOR_MUTED_PEACH
        if tracking_enabled and camera_enabled:
            status_text = "Monitoring... Active" if motion else "Monitoring... All clear."
            status_color = COLOR_ACCENT_CORAL if motion else COLOR_TEXT_BROWN
        
        cv2.putText(frame, status_text, (240, h - 32), cv2.FONT_HERSHEY_SIMPLEX, 0.6, status_color, 2, cv2.LINE_AA)

        if alerting and camera_enabled and tracking_enabled:
            cv2.circle(frame, (w - 40, h - 35), 12, COLOR_ACCENT_CORAL, -1)

        cv2.imshow('Taiyaki Guard', frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('y') and last_event_ts is not None:
            append_label(last_event_ts, user_id, picker_type, "picking")
            print("Labeled last event as picking")
        elif key == ord('n') and last_event_ts is not None:
            append_label(last_event_ts, user_id, picker_type, "not")
            print("Labeled last event as not picking")

    cap.release()
    cv2.destroyAllWindows()
    mp_face_mesh.close()
    mp_hands.close()

if __name__ == '__main__':
    run()
