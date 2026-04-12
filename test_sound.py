from playsound import playsound

try:
    playsound('alert.mp3')
    print("alert.mp3 played successfully")
except Exception as e:
    print(f"Error playing alert.mp3: {e}")