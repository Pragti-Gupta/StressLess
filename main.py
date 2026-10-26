import os
import warnings


# --- Environment & Silence Warnings ---
os.environ['TK_SILENCE_DEPRECATION'] = '1'
os.environ['GLOG_minloglevel'] = '2'
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '2'
warnings.filterwarnings("ignore", category=UserWarning, module="google.protobuf")


import cv2
import mediapipe as mp
import numpy as np
import time
import csv
import sys
import platform
import subprocess
import ctypes
from pathlib import Path

# Locate bundled read-only resources independently of the working directory.
RESOURCE_DIR = Path(__file__).resolve().parent

def resource_path(filename):
   return str(RESOURCE_DIR / filename)

from datetime import datetime, timedelta
from random import choice
from tkinter import messagebox
import tkinter as tk


# --- Settings / Config ---
sound_enabled = True
settings_window_open = False
food_log_window_open = False
sensitivity_level = "Medium"  # Options: "High", "Medium", "Low"
sensing_mode = "All"          # Options: "All" (Face + Nails + Hands), "Face+Hand" (No same-hand nail), "Face Only"


# Sensitivity Profile Definitions (Tuned to be less sensitive to false positives)
SENSITIVITY_PROFILES = {
   "High": {
       "dist_mesh": 0.125,       # Spatial boundary to trigger when hands are near face
       "dist_feature": 0.110,
       "dist_hand": 0.080,       # Increased hand-to-hand proximity threshold
       "same_hand_pinch": 0.200, # Increased same-hand pinch threshold
       "flex_std_thresh": 0.015, # Finger articulation threshold (used for face)
       "jitter_revs": 4,         # Minimum micro-reversals for face scratching
       "finger_osc_revs": 1,     # Highly responsive oscillation threshold for hand picking
       "stage1_time": 0.8,       # Quick soft nudge trigger
       "stage2_time": 2.0        # Quick popup intervention trigger
   },
   "Medium": {
       "dist_mesh": 0.095,
       "dist_feature": 0.075,
       "dist_hand": 0.065,       # Increased for reliable hand detection
       "same_hand_pinch": 0.170, # Increased for same-hand pinching / palm scratching
       "flex_std_thresh": 0.022,
       "jitter_revs": 5,
       "finger_osc_revs": 2,     # Balanced low oscillation threshold for reliable detection
       "stage1_time": 1.5,
       "stage2_time": 3.5
   },
   "Low": {
       "dist_mesh": 0.070,
       "dist_feature": 0.050,
       "dist_hand": 0.050,
       "same_hand_pinch": 0.140,
       "flex_std_thresh": 0.030,
       "jitter_revs": 6,
       "finger_osc_revs": 3,
       "stage1_time": 2.5,
       "stage2_time": 5.5
   }
}




# --- Native MP3 Audio Helper ---
def play_mp3(file_path):
   global sound_enabled
   if not sound_enabled:
       return


   # Prefer bundled resources; also accept an absolute path.
   candidates = [
       file_path if os.path.isabs(file_path) else resource_path(file_path),
   ]
   resolved_path = None
   for c in candidates:
       if c and os.path.exists(c):
           resolved_path = os.path.abspath(c)
           break


   if not resolved_path:
       print(f"Audio file not found: {file_path} (Resource directory: {RESOURCE_DIR})")
       return


   system = platform.system()
   try:
       if system == "Windows":
           alias = f"sound_{time.time_ns()}"
           # Get short path name on Windows if available to avoid space/hyphen issues in MCI
           buf = ctypes.create_unicode_buffer(512)
           if ctypes.windll.kernel32.GetShortPathNameW(resolved_path, buf, 512):
               win_path = buf.value
           else:
               win_path = resolved_path
           err = ctypes.windll.winmm.mciSendStringW(f'open "{win_path}" type mpegvideo alias {alias}', None, 0, 0)
           if err != 0:
               ctypes.windll.winmm.mciSendStringW(f'open "{ win_path }" alias {alias}', None, 0, 0)
           ctypes.windll.winmm.mciSendStringW(f'play {alias} from 0', None, 0, 0)
       elif system == "Darwin":
           subprocess.Popen(["afplay", resolved_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
       elif system == "Linux":
           # Try multiple common Linux CLI audio players
           for player in (["mpg123", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"], ["cvlc", "--play-and-exit", "-q"], ["paplay"]):
               try:
                   subprocess.Popen(player + [resolved_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                   break
               except FileNotFoundError:
                   continue
   except Exception as e:
       print(f"Audio playback error: {e}")




def play_alarm_sound():
   """Plays the picking alarm sound file when Stage 2 intervention triggers."""
   search_dirs = [str(RESOURCE_DIR)]


   # 1. Check explicitly for the attached bubble sound and standard alarm names first
   priority_names = [
       "alarm.mp3",
       "alert.mp3",
       "bell.mp3",
       "chime.mp3",
   ]
   for d in search_dirs:
       if not os.path.exists(d):
           continue
       for candidate in priority_names:
           full_p = os.path.join(d, candidate)
           if os.path.exists(full_p):
               play_mp3(full_p)
               return


   # 2. Fallback: play any .mp3 or .wav file dropped into the script folder
   for d in search_dirs:
       if not os.path.exists(d):
           continue
       for fname in os.listdir(d):
           if fname.lower().endswith((".mp3", ".wav")) and fname.lower() not in ("pop.mp3", "spin.mp3"):
               play_mp3(os.path.join(d, fname))
               return


   # 3. Last-resort system bell if no audio file is found on disk
   try:
       if platform.system() == "Windows":
           import winsound
           winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
       elif platform.system() == "Darwin":
           subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
   except Exception:
       pass




SUGGESTIONS = [
   "Grab a fidget spinner or toy!",
   "Squeeze a stress ball 5 times",
   "Tap your desk or play with a pen",
   "Take 3 deep, slow breaths",
   "Stretch your shoulders and hands",
]
DATA_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "StressLess", "data")
LOG_FILE = os.path.join(DATA_DIR, "picking_log.csv")
LABELS_FILE = os.path.join(DATA_DIR, "labels.csv")
FOOD_LOG_FILE = os.path.join(DATA_DIR, "food_log.csv")


COLOR_BG_CREAM     = (240, 247, 254)
COLOR_TEXT_BROWN   = (43, 50, 74) 
COLOR_ACCENT_CORAL = (71, 114, 241)
COLOR_MUTED_PEACH  = (211, 230, 253)
COLOR_WHITE        = (255, 255, 255)
COLOR_ALERT_RED    = (0, 0, 255)


tracking_enabled = True
camera_enabled = True
should_quit = False
alert_window_open = False


grace_period_until = 0.0
stage1_sound_played = False


current_suggestion = choice(SUGGESTIONS)
fidget_mode = False  
active_diversion = "menu"
fidget_start_time = None
lock_in_triggered = False


spinner_angle = 0.0  
spinner_speed = 0.0  
is_dragging_spinner = False
last_mouse_angle = 0.0
last_drag_time = 0.0
grabbed_leg_index = -1
bubble_sheet = []
food_entries = []
food_input_text = ""
food_input_active = False
trends_window_open = False
trends_text = []
main_win_w = 640
main_win_h = 480


# --- MAIN WINDOW HITBOXES ---
btn_tracking_box = [20, 20, 180, 100]
btn_camera_box   = [200, 20, 360, 100]
btn_settings_box = [380, 20, 460, 100]  # Settings Gear Icon
btn_food_log_box = [480, 20, 540, 100]  # NEW: Food Log Icon Box
btn_trends_box   = [560, 20, 620, 100]  # NEW: Habit Trends Box
btn_quit_box     = [640, 12, 735, 107]


# --- SETTINGS POPUP HITBOXES ---
btn_settings_audio_toggle = [30, 65, 370, 110]
btn_sens_high             = [30, 150, 130, 190]
btn_sens_med              = [145, 150, 255, 190]
btn_sens_low              = [270, 150, 370, 190]
btn_mode_all              = [30, 230, 130, 270]
btn_mode_face_hand        = [145, 230, 255, 270]
btn_mode_face_only        = [270, 230, 370, 270]
btn_settings_close        = [140, 290, 260, 330]


# --- FOOD LOG POPUP HITBOXES ---
btn_foodlog_input  = [30, 195, 370, 232]
btn_foodlog_add    = [50, 248, 170, 288]
btn_foodlog_close  = [230, 248, 350, 288]


btn_divert_box = [250, 220, 750, 310]
btn_choose_spinner   = [80, 300, 920, 390]
btn_choose_bubbles   = [80, 420, 920, 510]
btn_choose_breathing = [80, 540, 920, 630]


LIPS_INDICES = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291]
NOSE_TIP_INDEX = 1
LEFT_EYEBROW = [70, 63, 105, 66, 107]
RIGHT_EYEBROW = [336, 296, 334, 293, 300]
TARGET_FACIAL_INDICES = LIPS_INDICES + [NOSE_TIP_INDEX] + LEFT_EYEBROW + RIGHT_EYEBROW




def mouse_callback_trends(event, x, y, flags, param):
   global trends_window_open
   if event == cv2.EVENT_LBUTTONDOWN:
       # Close button hitbox for Trends window (in 520x240 base coordinates)
       if 200 <= x <= 320 and 180 <= y <= 220:
           trends_window_open = False


def ensure_data_dir():
   global DATA_DIR, LOG_FILE, LABELS_FILE, FOOD_LOG_FILE
   DATA_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "StressLess", "data")
   LOG_FILE = os.path.join(DATA_DIR, "picking_log.csv")
   LABELS_FILE = os.path.join(DATA_DIR, "labels.csv")
   FOOD_LOG_FILE = os.path.join(DATA_DIR, "food_log.csv")
   os.makedirs(DATA_DIR, exist_ok=True)




def show_habit_trends():
   ensure_data_dir()
   today = datetime.now().date()
   yesterday = today - timedelta(days=1)
  
   picking_today = 0
   picking_yesterday = 0
   if os.path.exists(LOG_FILE):
       with open(LOG_FILE, "r", encoding="utf-8") as f:
           reader = csv.DictReader(f)
           for row in reader:
               try:
                   ts = datetime.fromisoformat(row["timestamp"]).date()
                   if ts == today: picking_today += 1
                   elif ts == yesterday: picking_yesterday += 1
               except: pass
  
   food_today = 0
   food_yesterday = 0
   if os.path.exists(FOOD_LOG_FILE):
       with open(FOOD_LOG_FILE, "r", encoding="utf-8") as f:
           reader = csv.reader(f)
           for row in reader:
               if len(row) >= 1:
                   try:
                       ts = datetime.fromisoformat(row[0]).date()
                       if ts == today: food_today += 1
                       elif ts == yesterday: food_yesterday += 1
                   except: pass


   global trends_text, trends_window_open
   trends_text = []
  
   if picking_today > picking_yesterday:
       trends_text.append(f"Skin picking alerts increased ({picking_today} vs {picking_yesterday}).")
   elif picking_today < picking_yesterday:
       trends_text.append(f"Great job! Picking alerts decreased ({picking_today} vs {picking_yesterday}).")
   else:
       trends_text.append(f"Skin picking alerts are unchanged ({picking_today}).")
      
   if food_today > food_yesterday:
       trends_text.append(f"Food log entries increased ({food_today} vs {food_yesterday}).")
   elif food_today < food_yesterday:
       trends_text.append(f"Food log entries decreased ({food_today} vs {food_yesterday}).")
   else:
       trends_text.append(f"Food log entries are unchanged ({food_today}).")
      
   trends_window_open = True




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




def classify_hand_activity(history, current_time, profile, is_typing_posture, touching_face, touching_hand):
   """
   Differentiates between:
   - 'typing': fast multi-finger articulation or wide horizontal wrist travel without anchored picking
   - 'holding': hands resting on face or holding hands calmly
   - 'picking':
       * For face: localized finger articulation or rapid micro-scratching reversals while anchored
       * For hands/palms: picking, scratching, or rubbing between fingers and palms
   """
   if is_typing_posture:
       return "typing"


   recent = [e for e in history if current_time - e[0] <= 2.5]
   if len(recent) < 4:
       return "holding"


   xs = np.array([p[1] for p in recent])
   ys = np.array([p[2] for p in recent])
   flexions = np.array([p[3] for p in recent])
   wrist_xs = np.array([p[4] for p in recent])
   wrist_ys = np.array([p[5] for p in recent])
   pair_dists = np.array([p[6] for p in recent])


   # Check if the wrist/palm is gliding across space (e.g. reaching, waving, or moving across a keyboard)
   wrist_travel = np.hypot(np.ptp(wrist_xs), np.ptp(wrist_ys))
   if wrist_travel > 95.0:
       return "typing"


   # --- HAND / PALM PICKING & SCRATCHING (ONLY when not touching face) ---
   if touching_hand and not touching_face:
       # Must remain localized (hands anchored together)
       if np.hypot(np.ptp(xs), np.ptp(ys)) > 110.0:
           return "holding"


       # Measure frame-to-frame changes in relative distance
       d_pair = np.diff(pair_dists)
       osc_dir = np.sign(d_pair) * (np.abs(d_pair) > 0.001)
       nz_osc = osc_dir[osc_dir != 0]
       osc_revs = np.sum(nz_osc[1:] != nz_osc[:-1]) if len(nz_osc) > 1 else 0


       dx_h = np.diff(xs)
       dy_h = np.diff(ys)
       xh_dir = np.sign(dx_h) * (np.abs(dx_h) > 0.8)
       yh_dir = np.sign(dy_h) * (np.abs(dy_h) > 0.8)
       nz_xh = xh_dir[xh_dir != 0]
       nz_yh = yh_dir[yh_dir != 0]
       xy_revs = (np.sum(nz_xh[1:] != nz_xh[:-1]) if len(nz_xh) > 1 else 0) + (np.sum(nz_yh[1:] != nz_yh[:-1]) if len(nz_yh) > 1 else 0)


       # Trigger if oscillation or micro-motion threshold is met
       if (osc_revs >= profile["finger_osc_revs"] and np.ptp(pair_dists) > 0.004) or (xy_revs >= profile["finger_osc_revs"] + 1) or (np.std(pair_dists) > 0.005):
           return "picking"
       return "holding"


   # --- FACE DETECTION (UNTOUCHED) ---
   dx = np.diff(xs)
   dy = np.diff(ys)
   step_dists = np.hypot(dx, dy)
   total_path = np.sum(step_dists)


   flexion_std = np.std(flexions)
   flexion_range = np.ptp(flexions)


   if total_path < 28.0 and flexion_std < profile["flex_std_thresh"] * 0.85:
       return "holding"


   x_dir = np.sign(dx) * (np.abs(dx) > 1.8)
   y_dir = np.sign(dy) * (np.abs(dy) > 1.8)


   nz_x = x_dir[x_dir != 0]
   nz_y = y_dir[y_dir != 0]


   x_rev = np.sum(nz_x[1:] != nz_x[:-1]) if len(nz_x) > 1 else 0
   y_rev = np.sum(nz_y[1:] != nz_y[:-1]) if len(nz_y) > 1 else 0


   spatial_box = np.hypot(np.ptp(xs), np.ptp(ys))


   if spatial_box < 95.0:
       if (x_rev + y_rev) >= profile["jitter_revs"]:
           return "picking"
       if flexion_std > profile["flex_std_thresh"] or flexion_range > (profile["flex_std_thresh"] * 2.6):
           return "picking"


   return "holding"




def generate_bubble_wrap_hd():
   bubbles = []
   rows, cols = 3, 4
   start_x, start_y = 170, 320
   spacing_x, spacing_y = 220, 130
   for r in range(rows):
       for c in range(cols):
           bubbles.append({"x": start_x + (c * spacing_x), "y": start_y + (r * spacing_y), "r": 45, "popped": False, "burst_t": 0.0})
   return bubbles




def submit_food_entry():
   global food_input_text, food_entries
   item = food_input_text.strip()
   if item:
       now = datetime.now()
       timestamp = now.strftime("%I:%M %p")
       food_entries.append(f"{timestamp} - {item}")
       ensure_data_dir()
       with open(FOOD_LOG_FILE, "a", newline="", encoding="utf-8") as f:
           w = csv.writer(f)
           w.writerow([now.isoformat(), item])
       food_input_text = ""




def mouse_callback_main(event, x, y, flags, param):
   global tracking_enabled, camera_enabled, should_quit, settings_window_open, food_log_window_open, food_input_active, trends_window_open
   if event == cv2.EVENT_LBUTTONDOWN:
       # Route clicks to the centered 45% Habit Trends overlay if open
       if trends_window_open:
           tr_w = max(1, int(main_win_w * 0.45))
           tr_h = max(1, int(main_win_h * 0.45))
           ox = (main_win_w - tr_w) // 2
           oy = (main_win_h - tr_h) // 2
           if ox <= x <= ox + tr_w and oy <= y <= oy + tr_h:
               # Map click back to the 520x240 base coordinate space
               norm_x = int((x - ox) * 520 / tr_w)
               norm_y = int((y - oy) * 240 / tr_h)
               mouse_callback_trends(event, norm_x, norm_y, flags, param)
           return


       # Route clicks to the centered 45% Food Log overlay if open
       if food_log_window_open:
           fl_w = max(1, int(main_win_w * 0.45))
           fl_h = max(1, int(main_win_h * 0.45))
           ox = (main_win_w - fl_w) // 2
           oy = (main_win_h - fl_h) // 2
           if ox <= x <= ox + fl_w and oy <= y <= oy + fl_h:
               # Map click back to the 400x310 base coordinate space
               norm_x = int((x - ox) * 400 / fl_w)
               norm_y = int((y - oy) * 310 / fl_h)
               mouse_callback_food_log(event, norm_x, norm_y, flags, param)
           return


       if btn_tracking_box[0] <= x <= btn_tracking_box[2] and btn_tracking_box[1] <= y <= btn_tracking_box[3]:
           tracking_enabled = not tracking_enabled
       elif btn_camera_box[0] <= x <= btn_camera_box[2] and btn_camera_box[1] <= y <= btn_camera_box[3]:
           camera_enabled = not camera_enabled
       elif btn_settings_box[0] <= x <= btn_settings_box[2] and btn_settings_box[1] <= y <= btn_settings_box[3]:
           settings_window_open = True
       elif btn_food_log_box[0] <= x <= btn_food_log_box[2] and btn_food_log_box[1] <= y <= btn_food_log_box[3]:
           food_log_window_open = True
           trends_window_open = False
           food_input_active = True
       elif btn_trends_box[0] <= x <= btn_trends_box[2] and btn_trends_box[1] <= y <= btn_trends_box[3]:
           food_log_window_open = False
           food_input_active = False
           show_habit_trends()
       elif btn_quit_box[0] <= x <= btn_quit_box[2] and btn_quit_box[1] <= y <= btn_quit_box[3]:
           should_quit = True




def mouse_callback_settings(event, x, y, flags, param):
   global sound_enabled, settings_window_open, sensitivity_level, sensing_mode
   if event == cv2.EVENT_LBUTTONDOWN:
       if btn_settings_audio_toggle[0] <= x <= btn_settings_audio_toggle[2] and btn_settings_audio_toggle[1] <= y <= btn_settings_audio_toggle[3]:
           sound_enabled = not sound_enabled
       elif btn_sens_high[0] <= x <= btn_sens_high[2] and btn_sens_high[1] <= y <= btn_sens_high[3]:
           sensitivity_level = "High"
       elif btn_sens_med[0] <= x <= btn_sens_med[2] and btn_sens_med[1] <= y <= btn_sens_med[3]:
           sensitivity_level = "Medium"
       elif btn_sens_low[0] <= x <= btn_sens_low[2] and btn_sens_low[1] <= y <= btn_sens_low[3]:
           sensitivity_level = "Low"
       elif btn_mode_all[0] <= x <= btn_mode_all[2] and btn_mode_all[1] <= y <= btn_mode_all[3]:
           sensing_mode = "All"
       elif btn_mode_face_hand[0] <= x <= btn_mode_face_hand[2] and btn_mode_face_hand[1] <= y <= btn_mode_face_hand[3]:
           sensing_mode = "Face+Hand"
       elif btn_mode_face_only[0] <= x <= btn_mode_face_only[2] and btn_mode_face_only[1] <= y <= btn_mode_face_only[3]:
           sensing_mode = "Face Only"
       elif btn_settings_close[0] <= x <= btn_settings_close[2] and btn_settings_close[1] <= y <= btn_settings_close[3]:
           settings_window_open = False




def mouse_callback_food_log(event, x, y, flags, param):
   global food_log_window_open, food_input_active
   if event == cv2.EVENT_LBUTTONDOWN:
       if btn_foodlog_input[0] <= x <= btn_foodlog_input[2] and btn_foodlog_input[1] <= y <= btn_foodlog_input[3]:
           food_input_active = True
       elif btn_foodlog_add[0] <= x <= btn_foodlog_add[2] and btn_foodlog_add[1] <= y <= btn_foodlog_add[3]:
           submit_food_entry()
           food_input_active = True
       elif btn_foodlog_close[0] <= x <= btn_foodlog_close[2] and btn_foodlog_close[1] <= y <= btn_foodlog_close[3]:
           food_log_window_open = False
           food_input_active = False
       else:
           food_input_active = False




def mouse_callback_popup(event, x, y, flags, param):
   global fidget_mode, active_diversion, fidget_start_time, lock_in_triggered
   global spinner_speed, spinner_angle, is_dragging_spinner, last_mouse_angle, last_drag_time, grabbed_leg_index, bubble_sheet
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
               spinner_speed = 0.0
               is_dragging_spinner = False
               grabbed_leg_index = -1
           elif btn_choose_bubbles[0] <= x <= btn_choose_bubbles[2] and btn_choose_bubbles[1] <= y <= btn_choose_bubbles[3]:
               active_diversion = "bubbles"
               fidget_start_time = time.time()
               bubble_sheet = generate_bubble_wrap_hd()
           elif btn_choose_breathing[0] <= x <= btn_choose_breathing[2] and btn_choose_breathing[1] <= y <= btn_choose_breathing[3]:
               active_diversion = "breathing"
               fidget_start_time = time.time()
           return


       if fidget_mode and active_diversion == "spinner" and not lock_in_triggered:
           # Check if the user clicked on or near one of the 3 outer legs/arms of the spinner
           rad = spinner_angle * np.pi / 180.0
           clicked_leg = -1
           min_dist = float('inf')
          
           for i in range(3):
               branch_angle = rad + i * (2 * np.pi / 3)
               bx = cx + 110 * np.cos(branch_angle)
               by = cy + 110 * np.sin(branch_angle)
               mx = cx + 65 * np.cos(branch_angle)
               my = cy + 65 * np.sin(branch_angle)
              
               dist_lobe = np.hypot(x - bx, y - by)
               dist_arm = np.hypot(x - mx, y - my)
              
               # Extra-large hitbox covering the outer lobe and arm of each leg
               if dist_lobe <= 125 or dist_arm <= 85:
                   if dist_lobe < min_dist:
                       min_dist = dist_lobe
                       clicked_leg = i
          
           # Fallback: if clicked anywhere within the general spinner area (radius 220px), grab the closest leg
           if clicked_leg == -1 and np.hypot(x - cx, y - cy) <= 220:
               for i in range(3):
                   branch_angle = rad + i * (2 * np.pi / 3)
                   bx = cx + 110 * np.cos(branch_angle)
                   by = cy + 110 * np.sin(branch_angle)
                   dist_lobe = np.hypot(x - bx, y - by)
                   if dist_lobe < min_dist:
                       min_dist = dist_lobe
                       clicked_leg = i
          
           if clicked_leg != -1:
               is_dragging_spinner = True
               grabbed_leg_index = clicked_leg
               last_mouse_angle = np.arctan2(y - cy, x - cx) * 180.0 / np.pi
               last_drag_time = time.time()
               spinner_speed = 0.0  # Grabbing the leg stops current free-spin


       if fidget_mode and active_diversion == "bubbles" and not lock_in_triggered:
           for bubble in bubble_sheet:
               if not bubble["popped"] and np.hypot(x - bubble["x"], y - bubble["y"]) < bubble["r"]:
                   bubble["popped"] = True
                   bubble["burst_t"] = time.time()
                   play_mp3("pop.mp3")
                   break


   elif event == cv2.EVENT_MOUSEMOVE:
       if active_diversion == "spinner" and not lock_in_triggered:
           # Safety release if the left mouse button is no longer held down
           if not (flags & cv2.EVENT_FLAG_LBUTTON):
               is_dragging_spinner = False
               grabbed_leg_index = -1
               return


           if is_dragging_spinner:
               now = time.time()
               dt = max(now - last_drag_time, 0.004)
               current_mouse_angle = np.arctan2(y - cy, x - cx) * 180.0 / np.pi
               diff = current_mouse_angle - last_mouse_angle
               if diff > 180: diff -= 360
               elif diff < -180: diff += 360
              
               # Rotate the spinner directly with the dragged leg
               spinner_angle = (spinner_angle + diff) % 360.0
              
               # Calculate true angular velocity and blend smoothly for strong flick momentum
               inst_speed = (diff / dt) * 0.045
               if abs(diff) > 0.2:
                   spinner_speed = np.clip(0.35 * spinner_speed + 0.65 * inst_speed, -85.0, 85.0)
              
               last_mouse_angle = current_mouse_angle
               last_drag_time = now


               if abs(diff) > 6.0:
                   play_mp3("spin.mp3")


   elif event == cv2.EVENT_LBUTTONUP:
       if is_dragging_spinner:
           # If the mouse was held completely still for >0.15s before letting go, don't spin
           if time.time() - last_drag_time > 0.15:
               spinner_speed = 0.0
           elif abs(spinner_speed) > 1.5:
               # Boost momentum on release so flicking a leg feels fast and satisfying
               spinner_speed = np.clip(spinner_speed * 1.35, -85.0, 85.0)
               play_mp3("spin.mp3")
       is_dragging_spinner = False
       grabbed_leg_index = -1




def draw_fidget_spinner_hd(canvas, cx, cy, angle_deg):
   rad = angle_deg * np.pi / 180.0
   # Draw arms first so the outer leg weights and center cap sit cleanly on top
   for i in range(3):
       branch_angle = rad + i * (2 * np.pi / 3)
       bx = int(cx + 110 * np.cos(branch_angle))
       by = int(cy + 110 * np.sin(branch_angle))
       cv2.line(canvas, (cx, cy), (bx, by), COLOR_ACCENT_CORAL, 32, cv2.LINE_AA)


   for i in range(3):
       branch_angle = rad + i * (2 * np.pi / 3)
       bx = int(cx + 110 * np.cos(branch_angle))
       by = int(cy + 110 * np.sin(branch_angle))
      
       # Highlight the specific leg currently being grabbed and dragged
       if is_dragging_spinner and grabbed_leg_index == i:
           cv2.circle(canvas, (bx, by), 56, COLOR_WHITE, 4, cv2.LINE_AA)
          
       cv2.circle(canvas, (bx, by), 50, COLOR_ACCENT_CORAL, -1, cv2.LINE_AA)
       cv2.circle(canvas, (bx, by), 24, COLOR_TEXT_BROWN, -1, cv2.LINE_AA)
       cv2.circle(canvas, (bx, by), 12, COLOR_BG_CREAM, -1, cv2.LINE_AA)


   cv2.circle(canvas, (cx, cy), 40, COLOR_MUTED_PEACH, -1, cv2.LINE_AA)
   cv2.circle(canvas, (cx, cy), 32, COLOR_TEXT_BROWN, 4, cv2.LINE_AA)
   cv2.circle(canvas, (cx, cy), 12, COLOR_WHITE, -1, cv2.LINE_AA)




def draw_taiyaki_fish_hd(canvas, cx, cy, scale, img_mascot=None):
   if img_mascot is not None:
       try:
           orig_h, orig_w = img_mascot.shape[:2]
           base_max_dim = 280
           target_w = int(base_max_dim * scale) if orig_w > orig_h else int((orig_w / orig_h) * int(base_max_dim * scale))
           target_h = int((orig_h / orig_w) * target_w) if orig_w > orig_h else int(base_max_dim * scale)
           resized = cv2.resize(img_mascot, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
           x1, y1 = cx - (target_w // 2), cy - (target_h // 2)
           x2, y2 = x1 + target_w, y1 + target_h
           if x1 >= 0 and y1 >= 0 and x2 <= canvas.shape[1] and y2 <= canvas.shape[0]:
               if resized.shape[2] == 4:
                   alpha = np.expand_dims(resized[:, :, 3] / 255.0, axis=2)
                   canvas[y1:y2, x1:x2] = (resized[:, :, :3] * alpha + canvas[y1:y2, x1:x2] * (1 - alpha)).astype(np.uint8)
               else:
                   canvas[y1:y2, x1:x2] = resized
               return
       except Exception: pass


   cv2.ellipse(canvas, (cx, cy), (int(140 * scale), int(96 * scale)), 0, 0, 360, COLOR_ACCENT_CORAL, -1, cv2.LINE_AA)




def draw_image_button(frame, box, img):
   if img is None: return False
   x1, y1, x2, y2 = box
   w, h = x2 - x1, y2 - y1
   try:
       resized = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
       if resized.shape[2] == 4:
           alpha = np.expand_dims(resized[:, :, 3] / 255.0, axis=2)
           frame[y1:y2, x1:x2] = (resized[:, :, :3] * alpha + frame[y1:y2, x1:x2] * (1 - alpha)).astype(np.uint8)
       else:
           frame[y1:y2, x1:x2] = resized
       return True
   except Exception: return False




def draw_settings_gear_icon(frame, box):
   x1, y1, x2, y2 = box
   cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
   r_out, r_in = 24, 12
   cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_MUTED_PEACH, -1, cv2.LINE_AA)
   cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_ACCENT_CORAL, 2, cv2.LINE_AA)
   for i in range(8):
       angle = i * (np.pi / 4)
       px1 = int(cx + (r_out + 6) * np.cos(angle))
       py1 = int(cy + (r_out + 6) * np.sin(angle))
       cv2.line(frame, (cx, cy), (px1, py1), COLOR_TEXT_BROWN, 6, cv2.LINE_AA)
   cv2.circle(frame, (cx, cy), r_out, COLOR_MUTED_PEACH, -1, cv2.LINE_AA)
   cv2.circle(frame, (cx, cy), r_out, COLOR_TEXT_BROWN, 3, cv2.LINE_AA)
   cv2.circle(frame, (cx, cy), r_in, COLOR_BG_CREAM, -1, cv2.LINE_AA)


def draw_food_log_icon(frame, box):
   x1, y1, x2, y2 = box
   cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
   cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_MUTED_PEACH, -1, cv2.LINE_AA)
   cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_ACCENT_CORAL, 2, cv2.LINE_AA)
   # Draw simple list lines to represent a log
   for i in [-10, 0, 10]:
       cv2.line(frame, (cx - 15, cy + i), (cx + 15, cy + i), COLOR_TEXT_BROWN, 3, cv2.LINE_AA)


def draw_trends_icon(frame, box):
   x1, y1, x2, y2 = box
   cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
   cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_MUTED_PEACH, -1, cv2.LINE_AA)
   cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_ACCENT_CORAL, 2, cv2.LINE_AA)
   # Draw a little line graph trend icon
   cv2.line(frame, (cx - 15, cy + 10), (cx - 5, cy), COLOR_TEXT_BROWN, 3, cv2.LINE_AA)
   cv2.line(frame, (cx - 5, cy), (cx + 5, cy + 5), COLOR_TEXT_BROWN, 3, cv2.LINE_AA)
   cv2.line(frame, (cx + 5, cy + 5), (cx + 15, cy - 10), COLOR_TEXT_BROWN, 3, cv2.LINE_AA)
   cv2.circle(frame, (cx + 15, cy - 10), 3, COLOR_ACCENT_CORAL, -1, cv2.LINE_AA)




# --- Main Application Loop ---
def run(user_id="anonymous", picker_type="skin picking"):
   global tracking_enabled, camera_enabled, sound_enabled, should_quit, alert_window_open
   global current_suggestion, fidget_mode, active_diversion, fidget_start_time, lock_in_triggered
   global spinner_angle, spinner_speed, is_dragging_spinner, bubble_sheet
   global grace_period_until, stage1_sound_played, settings_window_open, sensitivity_level, sensing_mode
   global food_log_window_open, food_entries, food_input_text, food_input_active, trends_window_open, trends_text
   global main_win_w, main_win_h


   ensure_data_dir()
  
   # Load today's food log entries on startup
   if os.path.exists(FOOD_LOG_FILE):
       today_date = datetime.now().date()
       with open(FOOD_LOG_FILE, "r", encoding="utf-8") as f:
           reader = csv.reader(f)
           for row in reader:
               if len(row) >= 2:
                   try:
                       dt = datetime.fromisoformat(row[0])
                       if dt.date() == today_date:
                           food_entries.append(f"{dt.strftime('%I:%M %p')} - {row[1]}")
                   except: pass


   root = tk.Tk()
   screen_w = root.winfo_screenwidth()
   screen_h = root.winfo_screenheight()
   root.withdraw()
   cap = cv2.VideoCapture(0)
   if not cap.isOpened():
       messagebox.showerror("Camera Error", "Taiyaki Guard could not access your webcam.")
       return


   img_video_on = cv2.imread(resource_path("videoon.png"), -1)
   img_video_off = cv2.imread(resource_path("videooff.png"), -1)
   img_tracking_on = cv2.imread(resource_path("trackingon.png"), -1)
   img_tracking_off = cv2.imread(resource_path("trackingoff.png"), -1)
   img_quit = cv2.imread(resource_path("quit.png"), -1)
   img_taiyaki_mascot = cv2.imread(resource_path("taiyaki.png"), -1)
   img_food_log = cv2.imread(resource_path("foodlog.png"), -1)


   mp_face_mesh = mp.solutions.face_mesh.FaceMesh(max_num_faces=1, refine_landmarks=True, min_detection_confidence=0.5, min_tracking_confidence=0.5)
   mp_hands = mp.solutions.hands.Hands(max_num_hands=2, min_detection_confidence=0.5, min_tracking_confidence=0.5)
   TIP_IDS = [4, 8, 12, 16, 20]
   MCP_IDS = [2, 5, 9, 13, 17]
   PALM_IDS = [0, 1, 5, 9, 13, 17] # Added palm and base knuckle tracking for palm scratching/rubbing


   finger_history = []
   per_finger_prev = {}
   continuous_touch_start = None
   alert_until = 0
   last_event_ts = None
   settings_active_prev = False
   food_log_active_prev = False
   trends_active_prev = False


   # Open as a standard resizable window
   cv2.namedWindow('Taiyaki Guard', cv2.WINDOW_NORMAL)
   cv2.setMouseCallback('Taiyaki Guard', mouse_callback_main)


   while True:
       if should_quit or cv2.getWindowProperty('Taiyaki Guard', cv2.WND_PROP_VISIBLE) < 1:
           break


       ret, frame = cap.read()
       if not ret:
           cv2.waitKey(10)
           time.sleep(0.05)
           continue
      
       frame = cv2.flip(frame, 1)


       # Dynamically match the current window size if the user resizes the window
       target_w, target_h = screen_w, screen_h
       try:
           rect = cv2.getWindowImageRect('Taiyaki Guard')
           if rect and len(rect) == 4 and rect[2] > 100 and rect[3] > 100:
               target_w, target_h = rect[2], rect[3]
       except Exception:
           pass


       frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
       h, w = frame.shape[:2]
       main_win_w, main_win_h = w, h
       current_time = time.time()


       # Fetch active sensitivity profile
       profile = SENSITIVITY_PROFILES[sensitivity_level]


       if not camera_enabled:
           frame = np.zeros((h, w, 3), dtype=np.uint8)
           frame[:] = COLOR_BG_CREAM
           cv2.putText(frame, "Monitoring Paused", (int(w/2) - 140, int(h/2)), cv2.FONT_HERSHEY_DUPLEX, 0.8, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)
      
       touching = False
       touching_face = False
       touching_hand = False
       motion = False
       activity_state = "clear"
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
                           cv2.circle(frame, (int(lm.x * w), int(lm.y * h)), 2, COLOR_ACCENT_CORAL, -1)


           touching_tips_px = []
           touching_flexions = []
           touching_wrists_px = []
           touching_pair_dists = []
           is_typing_posture = False


           if hand_res.multi_hand_landmarks:
               hands_list = hand_res.multi_hand_landmarks


               fast_moving_fingers = 0
               face_lowest_y = max([p[1] for p in all_face_points], default=0.45)


               for h_idx, hand in enumerate(hands_list):
                   wrist = hand.landmark[0]
                   wrist_px = (wrist.x * w, wrist.y * h)
                   middle_mcp = hand.landmark[9]
                   hand_scale = max(np.hypot(wrist.x - middle_mcp.x, wrist.y - middle_mcp.y), 0.01)


                   for i, tid in enumerate(TIP_IDS):
                       lm = hand.landmark[tid]
                       key_id = (h_idx, tid)
                       if key_id in per_finger_prev:
                           prev_x, prev_y, prev_t = per_finger_prev[key_id]
                           dt_f = max(current_time - prev_t, 0.005)
                           vel_y = abs(lm.y - prev_y) / dt_f
                           if vel_y > 0.38 and lm.y > (face_lowest_y + 0.04):
                               fast_moving_fingers += 1
                       per_finger_prev[key_id] = (lm.x, lm.y, current_time)


                   for i, tid in enumerate(TIP_IDS):
                       lm = hand.landmark[tid]
                       mcp = hand.landmark[MCP_IDS[i]]
                       tx, ty = int(lm.x * w), int(lm.y * h)
                      
                       tip_touching_face = False
                       tip_touching_hand = False
                       contact_pair_dist = 0.0


                       # 1. Check if this specific fingertip is touching the face
                       if all_face_points:
                           for f_point in all_face_points[::8]:
                               if np.sqrt((lm.x - f_point[0])**2 + (lm.y - f_point[1])**2 + (lm.z - f_point[2])**2) < profile["dist_mesh"]:
                                   tip_touching_face = True
                                   break
                          
                           for idx, (fx_norm, fy_norm, fz_norm, fx_px, fy_px) in face_landmarks_dict.items():
                               if np.sqrt((lm.x - fx_norm)**2 + (lm.y - fy_norm)**2 + (lm.z - fz_norm)**2) < profile["dist_feature"]:
                                   tip_touching_face = True
                                   cv2.circle(frame, (fx_px, fy_px), 6, COLOR_ALERT_RED, -1)


                       # 2. Check if this fingertip is picking/scratching at fingers, nails, or palm on the OTHER hand
                       if sensing_mode in ("All", "Face+Hand"):
                           for other_idx, other_hand in enumerate(hands_list):
                               if other_idx == h_idx:
                                   continue
                               other_wrist = other_hand.landmark[0]
                               if np.hypot(wrist.x - other_wrist.x, wrist.y - other_wrist.y) > 0.38:
                                   continue
                               # Check against fingers (2..20) AND palm landmarks (0, 1, 5, 9, 13, 17) for palm scratching/rubbing
                               for o_idx in list(range(2, 21)) + PALM_IDS:
                                   o_lm = other_hand.landmark[o_idx]
                                   dist_2d = np.hypot(lm.x - o_lm.x, lm.y - o_lm.y)
                                   if dist_2d < profile["dist_hand"]:
                                       tip_touching_hand = True
                                       contact_pair_dist = (np.hypot(lm.x - o_lm.x, lm.y - o_lm.y) + np.hypot(mcp.x - o_lm.x, mcp.y - o_lm.y)) / hand_scale
                                       cv2.circle(frame, (int(o_lm.x * w), int(o_lm.y * h)), 5, COLOR_ALERT_RED, -1)
                                       break
                               if tip_touching_hand:
                                   break


                       # 3. Check for same-hand cuticle / nail / palm picking
                       if sensing_mode == "All" and not tip_touching_hand and i == 0:
                           for j, other_tid in enumerate(TIP_IDS):
                               if j == 0:
                                   continue
                               other_tip = hand.landmark[other_tid]
                               other_dip = hand.landmark[other_tid - 1]
                               other_pip = hand.landmark[other_tid - 2]
                               d_tip = np.hypot(lm.x - other_tip.x, lm.y - other_tip.y) / hand_scale
                               d_dip = np.hypot(lm.x - other_dip.x, lm.y - other_dip.y) / hand_scale
                               d_pip = np.hypot(lm.x - other_pip.x, lm.y - other_pip.y) / hand_scale
                               min_d = min(d_tip, d_dip, d_pip)
                               if min_d < profile["same_hand_pinch"]:
                                   tip_touching_hand = True
                                   contact_pair_dist = d_tip + (np.hypot(mcp.x - other_tip.x, mcp.y - other_tip.y) / hand_scale)
                                   cv2.circle(frame, (int(other_tip.x * w), int(other_tip.y * h)), 5, COLOR_ALERT_RED, -1)
                                   break


                       tip_touching = tip_touching_face or tip_touching_hand


                       cv2.line(frame, (int(mcp.x * w), int(mcp.y * h)), (tx, ty), COLOR_ACCENT_CORAL if tip_touching else COLOR_MUTED_PEACH, 2)
                       cv2.circle(frame, (tx, ty), 6 if tip_touching else 4, COLOR_ALERT_RED if tip_touching else COLOR_MUTED_PEACH, -1)


                       if tip_touching:
                           touching = True
                           if tip_touching_face: touching_face = True
                           if tip_touching_hand: touching_hand = True
                           touching_tips_px.append((tx, ty))
                           touching_wrists_px.append(wrist_px)
                           tip_to_wrist = np.hypot(lm.x - wrist.x, lm.y - wrist.y) / hand_scale
                           tip_to_mcp = np.hypot(lm.x - mcp.x, lm.y - mcp.y) / hand_scale
                           touching_flexions.append(tip_to_wrist + tip_to_mcp)
                           touching_pair_dists.append(contact_pair_dist)


               if fast_moving_fingers >= 3 and not touching_face:
                   is_typing_posture = True


           if current_time < grace_period_until:
               touching = False
               touching_face = False
               touching_hand = False


           if touching:
               if continuous_touch_start is None: continuous_touch_start = current_time
               if touching_tips_px:
                   avg_x = float(np.mean([p[0] for p in touching_tips_px]))
                   avg_y = float(np.mean([p[1] for p in touching_tips_px]))
                   avg_flex = float(np.mean(touching_flexions))
                   avg_wx = float(np.mean([p[0] for p in touching_wrists_px]))
                   avg_wy = float(np.mean([p[1] for p in touching_wrists_px]))
                   avg_pair_d = float(np.mean(touching_pair_dists)) if touching_pair_dists else 0.0
                   finger_history.append((current_time, avg_x, avg_y, avg_flex, avg_wx, avg_wy, avg_pair_d))
               if len(finger_history) > 180: finger_history = finger_history[-180:]
           else:
               continuous_touch_start = None
               finger_history = []
               stage1_sound_played = False


           if touching:
               activity_state = classify_hand_activity(
                   finger_history, current_time, profile, is_typing_posture, touching_face, touching_hand
               )
           elif is_typing_posture:
               activity_state = "typing"


           motion = (activity_state == "picking")


           if touching and not motion and continuous_touch_start is not None:
               if (current_time - continuous_touch_start) > 1.2:
                   continuous_touch_start = current_time - 0.8


           duration = (current_time - continuous_touch_start) if (continuous_touch_start and motion) else 0.0


           # --- STAGE 1: SOFT NUDGE ---
           if duration >= profile["stage1_time"] and duration < profile["stage2_time"] and motion and current_time >= alert_until:
               overlay_border = frame.copy()
               cv2.rectangle(overlay_border, (0, 0), (w, h), COLOR_ALERT_RED, 20)
               cv2.addWeighted(overlay_border, 0.4, frame, 0.6, 0, frame)
              
               if not stage1_sound_played:
                   play_alarm_sound()
                   stage1_sound_played = True


           # --- STAGE 2: FULL POPUP INTERVENTION ---
           if duration >= profile["stage2_time"] and motion and current_time >= alert_until:
               alert_until = current_time + 8.0
               last_event_ts = datetime.utcnow().isoformat()
               detected_type = "hand/nail/palm picking" if (touching_hand and not touching_face) else picker_type
               log_event(last_event_ts, user_id, detected_type, "alert", duration, motion)
               play_alarm_sound()
               current_suggestion = choice(SUGGESTIONS)
               fidget_mode = False
               active_diversion = "menu"
               lock_in_triggered = False
       else:
           continuous_touch_start = None
           finger_history = []


       if fidget_mode and active_diversion != "menu" and fidget_start_time is not None:
           if current_time - fidget_start_time < 18.0: alert_until = current_time + 1.0
       elif fidget_mode and active_diversion == "menu":
           alert_until = current_time + 1.0


       alerting = current_time < alert_until


       # --- Settings Modal Window ---
       if settings_window_open:
           set_w, set_h = 400, 350
           settings_frame = np.zeros((set_h, set_w, 3), dtype=np.uint8)
           settings_frame[:] = COLOR_BG_CREAM
           cv2.rectangle(settings_frame, (0, 0), (set_w, set_h), COLOR_ACCENT_CORAL, 4)


           cv2.putText(settings_frame, "SETTINGS", (135, 36), cv2.FONT_HERSHEY_DUPLEX, 0.8, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)
           cv2.line(settings_frame, (20, 48), (380, 48), COLOR_MUTED_PEACH, 2, cv2.LINE_AA)


           # Audio Toggle Button
           audio_bg = COLOR_ACCENT_CORAL if sound_enabled else COLOR_MUTED_PEACH
           audio_fg = COLOR_WHITE if sound_enabled else COLOR_TEXT_BROWN
           cv2.rectangle(settings_frame, (btn_settings_audio_toggle[0], btn_settings_audio_toggle[1]),
                         (btn_settings_audio_toggle[2], btn_settings_audio_toggle[3]), audio_bg, -1, cv2.LINE_AA)
           cv2.putText(settings_frame, f"Audio Cues: {'ON' if sound_enabled else 'OFF'}",
                       (btn_settings_audio_toggle[0] + 75, btn_settings_audio_toggle[1] + 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.65, audio_fg, 2, cv2.LINE_AA)


           # Sensitivity Title
           cv2.putText(settings_frame, "Detection Sensitivity:", (30, 138), cv2.FONT_HERSHEY_SIMPLEX, 0.58, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)


           # High / Medium / Low Selector Buttons
           for btn_box, lvl_name in [(btn_sens_high, "High"), (btn_sens_med, "Medium"), (btn_sens_low, "Low")]:
               is_selected = (sensitivity_level == lvl_name)
               btn_bg = COLOR_ACCENT_CORAL if is_selected else COLOR_MUTED_PEACH
               btn_fg = COLOR_WHITE if is_selected else COLOR_TEXT_BROWN
               cv2.rectangle(settings_frame, (btn_box[0], btn_box[1]), (btn_box[2], btn_box[3]), btn_bg, -1, cv2.LINE_AA)
               cv2.putText(settings_frame, lvl_name, (btn_box[0] + 18, btn_box[1] + 26),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.55, btn_fg, 2, cv2.LINE_AA)


           # Sensing Mode Title
           cv2.putText(settings_frame, "Finger Sensing Mode:", (30, 218), cv2.FONT_HERSHEY_SIMPLEX, 0.58, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)


           # Sensing Mode Selector Buttons (All / Face+Hand / Face Only)
           for btn_box, m_name, m_label in [
               (btn_mode_all, "All", "All"),
               (btn_mode_face_hand, "Face+Hand", "Face+Hand"),
               (btn_mode_face_only, "Face Only", "Face Only")
           ]:
               is_selected = (sensing_mode == m_name)
               btn_bg = COLOR_ACCENT_CORAL if is_selected else COLOR_MUTED_PEACH
               btn_fg = COLOR_WHITE if is_selected else COLOR_TEXT_BROWN
               cv2.rectangle(settings_frame, (btn_box[0], btn_box[1]), (btn_box[2], btn_box[3]), btn_bg, -1, cv2.LINE_AA)
               cv2.putText(settings_frame, m_label, (btn_box[0] + 8, btn_box[1] + 26),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.48, btn_fg, 2, cv2.LINE_AA)


           # Close Button
           cv2.rectangle(settings_frame, (btn_settings_close[0], btn_settings_close[1]),
                         (btn_settings_close[2], btn_settings_close[3]), COLOR_TEXT_BROWN, -1, cv2.LINE_AA)
           cv2.putText(settings_frame, "Close", (btn_settings_close[0] + 34, btn_settings_close[1] + 27),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.65, COLOR_WHITE, 2, cv2.LINE_AA)


           cv2.imshow('Settings', settings_frame)
           if not settings_active_prev:
               cv2.setWindowProperty('Settings', cv2.WND_PROP_TOPMOST, 1)
               cv2.setMouseCallback('Settings', mouse_callback_settings)
               settings_active_prev = True
       else:
           if settings_active_prev:
               cv2.destroyWindow('Settings')
               settings_active_prev = False


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
               for btn, name in [(btn_choose_spinner, "1. Kinetic Fidget Spinner"), (btn_choose_bubbles, "2. Pop Bubble Wrap (HD Grid)"), (btn_choose_breathing, "3. Taiyaki Breathing Guide (Synced)")]:
                   cv2.rectangle(alert_frame, (btn[0], btn[1]), (btn[2], btn[3]), COLOR_ACCENT_CORAL, -1, cv2.LINE_AA)
                   cv2.putText(alert_frame, name, (btn[0] + 40, btn[1] + 55), cv2.FONT_HERSHEY_SIMPLEX, 1.0, COLOR_WHITE, 3, cv2.LINE_AA)
           else:
               time_left = 15 - int(current_time - fidget_start_time)
               if time_left > 0:
                   cv2.putText(alert_frame, f"Time left: {time_left}s", (750, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.0, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)
                   if active_diversion == "spinner":
                       if not is_dragging_spinner:
                           spinner_angle = (spinner_angle + spinner_speed) % 360.0
                           spinner_speed *= 0.985  # Low-friction bearing glide
                           if abs(spinner_speed) < 0.05: spinner_speed = 0.0
                       cv2.putText(alert_frame, "Grab & Flick any of the 3 Spinner Legs to SPIN!", (190, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)
                       draw_fidget_spinner_hd(alert_frame, 500, 450, spinner_angle)
                   elif active_diversion == "bubbles":
                       cv2.putText(alert_frame, "Click the sheet bubbles to pop them!", (220, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.8, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)
                       for b in bubble_sheet:
                           if not b["popped"]:
                               cv2.circle(alert_frame, (b["x"], b["y"]), b["r"], COLOR_MUTED_PEACH, -1, cv2.LINE_AA)
                               cv2.circle(alert_frame, (b["x"], b["y"]), b["r"] - 4, COLOR_WHITE, 2, cv2.LINE_AA)
                           else:
                               cv2.circle(alert_frame, (b["x"], b["y"]), b["r"], COLOR_TEXT_BROWN, 2, cv2.LINE_AA)
                   elif active_diversion == "breathing":
                       scale_factor = 0.85 + 0.45 * np.sin((current_time - fidget_start_time) * (2 * np.pi / 8.0))
                       breathe_label = "BREATHE IN..." if np.cos((current_time - fidget_start_time) * (2 * np.pi / 8.0)) >= 0 else "BREATHE OUT..."
                       cv2.putText(alert_frame, breathe_label, (360, 240), cv2.FONT_HERSHEY_DUPLEX, 1.1, COLOR_TEXT_BROWN, 3, cv2.LINE_AA)
                       cv2.circle(alert_frame, (500, 450), int(210 * scale_factor), COLOR_MUTED_PEACH, 4, cv2.LINE_AA)
                       draw_taiyaki_fish_hd(alert_frame, 500, 450, scale_factor, img_mascot=img_taiyaki_mascot)
               else:
                   lock_in_triggered = True
                   cv2.rectangle(alert_frame, (100, 260), (900, 520), COLOR_MUTED_PEACH, -1, cv2.LINE_AA)
                   cv2.rectangle(alert_frame, (100, 260), (900, 520), COLOR_ACCENT_CORAL, 4, cv2.LINE_AA)
                   cv2.putText(alert_frame, "URGE RIDDEN OUT!", (285, 360), cv2.FONT_HERSHEY_DUPLEX, 1.5, COLOR_TEXT_BROWN, 4, cv2.LINE_AA)


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
               grace_period_until = current_time + 8.0


       # --- Dashboard Controls ---
       if not draw_image_button(frame, btn_tracking_box, img_tracking_on if tracking_enabled else img_tracking_off):
           cv2.rectangle(frame, (btn_tracking_box[0], btn_tracking_box[1]), (btn_tracking_box[2], btn_tracking_box[3]), COLOR_ACCENT_CORAL if tracking_enabled else COLOR_MUTED_PEACH, -1, cv2.LINE_AA)


       if not draw_image_button(frame, btn_camera_box, img_video_on if camera_enabled else img_video_off):
           cv2.rectangle(frame, (btn_camera_box[0], btn_camera_box[1]), (btn_camera_box[2], btn_camera_box[3]), COLOR_ACCENT_CORAL if camera_enabled else COLOR_MUTED_PEACH, -1, cv2.LINE_AA)


       # Settings Gear Icon Button
       draw_settings_gear_icon(frame, btn_settings_box)
      
       # Food Log Icon Button
       if not draw_image_button(frame, btn_food_log_box, img_food_log):
           draw_food_log_icon(frame, btn_food_log_box)


       # Habit Trends Icon Button
       draw_trends_icon(frame, btn_trends_box)


       if not draw_image_button(frame, btn_quit_box, img_quit):
           cv2.rectangle(frame, (btn_quit_box[0], btn_quit_box[1]), (btn_quit_box[2], btn_quit_box[3]), COLOR_TEXT_BROWN, -1, cv2.LINE_AA)


       # --- Footer ---
       overlay = frame.copy()
       cv2.rectangle(overlay, (0, h - 75), (w, h), COLOR_BG_CREAM, -1)
       cv2.addWeighted(overlay, 0.85, frame, 0.15, 0, frame)
       cv2.line(frame, (0, h - 75), (w, h - 75), COLOR_MUTED_PEACH, 2, cv2.LINE_AA)
       cv2.putText(frame, f"Timer: {duration:.1f}s | Sens: {sensitivity_level} | Mode: {sensing_mode}", (20, h - 32), cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)


       if current_time < grace_period_until:
           status_text = "Status: Grace Period"
       elif activity_state == "typing":
           status_text = "Status: Fast Typing / Moving (Safe)"
       elif touching and motion:
           if touching_hand and not touching_face:
               status_text = "Status: Hand / Nail Picking Detected!"
           else:
               status_text = "Status: Face Scratching / Picking Detected!"
       elif touching and not motion:
           if touching_hand and not touching_face:
               status_text = "Status: Holding Hands / Resting (Safe)"
           else:
               status_text = "Status: Resting Hand on Face (Safe)"
       else:
           status_text = "Status: Monitoring... All clear."


       cv2.putText(frame, status_text, (430, h - 32), cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_ALERT_RED if (touching and motion) else COLOR_TEXT_BROWN, 2, cv2.LINE_AA)


       # --- Centered Food Log Modal Overlay (45% of Main Window) ---
       if food_log_window_open:
           base_w, base_h = 400, 310
           fl_frame = np.zeros((base_h, base_w, 3), dtype=np.uint8)
           fl_frame[:] = COLOR_BG_CREAM
           cv2.rectangle(fl_frame, (0, 0), (base_w, base_h), COLOR_ACCENT_CORAL, 4)


           cv2.putText(fl_frame, "FOOD LOG", (135, 38), cv2.FONT_HERSHEY_DUPLEX, 0.8, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)
           cv2.line(fl_frame, (20, 50), (380, 50), COLOR_MUTED_PEACH, 2, cv2.LINE_AA)


           y_offset = 78
           if not food_entries:
               cv2.putText(fl_frame, "No entries yet today.", (30, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_TEXT_BROWN, 1, cv2.LINE_AA)
           else:
               for entry in food_entries[-4:]:
                   cv2.putText(fl_frame, entry[:36], (30, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_TEXT_BROWN, 1, cv2.LINE_AA)
                   y_offset += 26


           # Direct Text Input Box
           ix1, iy1, ix2, iy2 = btn_foodlog_input
           cv2.rectangle(fl_frame, (ix1, iy1), (ix2, iy2), COLOR_WHITE, -1, cv2.LINE_AA)
           border_col = COLOR_ACCENT_CORAL if food_input_active else COLOR_MUTED_PEACH
           cv2.rectangle(fl_frame, (ix1, iy1), (ix2, iy2), border_col, 2, cv2.LINE_AA)


           if food_input_text or food_input_active:
               cursor = "|" if (food_input_active and int(current_time * 2) % 2 == 0) else ""
               shown_text = (food_input_text[-26:] if len(food_input_text) > 26 else food_input_text) + cursor
               cv2.putText(fl_frame, shown_text, (ix1 + 10, iy1 + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_TEXT_BROWN, 1, cv2.LINE_AA)
           else:
               cv2.putText(fl_frame, "Click here & type food...", (ix1 + 10, iy1 + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (140, 145, 165), 1, cv2.LINE_AA)


           # Add Button
           cv2.rectangle(fl_frame, (btn_foodlog_add[0], btn_foodlog_add[1]),
                         (btn_foodlog_add[2], btn_foodlog_add[3]), COLOR_ACCENT_CORAL, -1, cv2.LINE_AA)
           cv2.putText(fl_frame, "+ Add", (btn_foodlog_add[0] + 32, btn_foodlog_add[1] + 27),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.60, COLOR_WHITE, 2, cv2.LINE_AA)


           # Close Button
           cv2.rectangle(fl_frame, (btn_foodlog_close[0], btn_foodlog_close[1]),
                         (btn_foodlog_close[2], btn_foodlog_close[3]), COLOR_TEXT_BROWN, -1, cv2.LINE_AA)
           cv2.putText(fl_frame, "Close", (btn_foodlog_close[0] + 35, btn_foodlog_close[1] + 27),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.60, COLOR_WHITE, 2, cv2.LINE_AA)


           # Scale modal to 45% of the current window dimensions and composite in the exact center
           fl_w = max(1, int(w * 0.45))
           fl_h = max(1, int(h * 0.45))
           fl_resized = cv2.resize(fl_frame, (fl_w, fl_h), interpolation=cv2.INTER_LINEAR)


           dark_bg = np.zeros_like(frame)
           cv2.addWeighted(frame, 0.6, dark_bg, 0.4, 0, frame)
           ox = max(0, (w - fl_w) // 2)
           oy = max(0, (h - fl_h) // 2)
           frame[oy:oy+fl_h, ox:ox+fl_w] = fl_resized


       # --- Centered Habit Trends Modal Overlay (45% of Main Window) ---
       if trends_window_open:
           base_w, base_h = 520, 240
           tr_frame = np.zeros((base_h, base_w, 3), dtype=np.uint8)
           tr_frame[:] = COLOR_BG_CREAM
           cv2.rectangle(tr_frame, (0, 0), (base_w, base_h), COLOR_ACCENT_CORAL, 4)


           cv2.putText(tr_frame, "HABIT TRENDS", (170, 38), cv2.FONT_HERSHEY_DUPLEX, 0.8, COLOR_TEXT_BROWN, 2, cv2.LINE_AA)
           cv2.line(tr_frame, (20, 50), (500, 50), COLOR_MUTED_PEACH, 2, cv2.LINE_AA)


           y_offset = 90
           for line in trends_text:
               cv2.putText(tr_frame, line, (25, y_offset), cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_TEXT_BROWN, 1, cv2.LINE_AA)
               y_offset += 35


           # Close Button
           cv2.rectangle(tr_frame, (200, 180), (320, 220), COLOR_TEXT_BROWN, -1, cv2.LINE_AA)
           cv2.putText(tr_frame, "Close", (232, 206), cv2.FONT_HERSHEY_SIMPLEX, 0.65, COLOR_WHITE, 2, cv2.LINE_AA)


           # Scale modal to 45% of the current window dimensions and composite in the exact center
           tr_w = max(1, int(w * 0.45))
           tr_h = max(1, int(h * 0.45))
           tr_resized = cv2.resize(tr_frame, (tr_w, tr_h), interpolation=cv2.INTER_LINEAR)


           dark_bg = np.zeros_like(frame)
           cv2.addWeighted(frame, 0.6, dark_bg, 0.4, 0, frame)
           ox = max(0, (w - tr_w) // 2)
           oy = max(0, (h - tr_h) // 2)
           frame[oy:oy+tr_h, ox:ox+tr_w] = tr_resized


       cv2.imshow('Taiyaki Guard', frame)
       key = cv2.waitKey(1) & 0xFF
       if key == 27:
           if food_log_window_open or trends_window_open:
               food_log_window_open = False
               trends_window_open = False
               food_input_active = False
           else:
               break
       elif food_log_window_open and food_input_active and key != 255:
           if key in (10, 13):  # Enter key submits the food item
               submit_food_entry()
           elif key in (8, 127):  # Backspace / Delete
               food_input_text = food_input_text[:-1]
           elif 32 <= key <= 126 and len(food_input_text) < 45:
               food_input_text += chr(key)


   cap.release()
   cv2.destroyAllWindows()
   mp_face_mesh.close()
   mp_hands.close()




if __name__ == '__main__':
   run()
