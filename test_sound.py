from playsound import playsound

try:
    playsound('alert.mov')
    print("alert.mov played successfully")
except Exception as e:
    print(f"Error playing alert.mov: {e}")
