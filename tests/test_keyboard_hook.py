import keyboard, time, threading, sys

print("Press any key within 5 seconds...")
captured = []
done = threading.Event()

def callback(e):
    if e.event_type == 'down':
        captured.append(e.name)
        print(f"Key pressed: {e.name}")
        done.set()

hook = keyboard.hook(callback, suppress=True)
done.wait(timeout=5)
keyboard.unhook_all()

if captured:
    print(f"SUCCESS: Captured keys: {captured}")
    sys.exit(0)
else:
    print("FAIL: No keys captured")
    sys.exit(1)
