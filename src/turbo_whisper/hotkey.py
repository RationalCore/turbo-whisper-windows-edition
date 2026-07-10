"""Global hotkey handling with platform-specific backends.

On Windows uses WinAPI RegisterHotKey to avoid blocking all keyboard input.
On Linux/macOS falls back to pynput.
"""

import logging
import os
import sys
import threading
import time
from typing import Callable


# Setup logging
def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("turbo_whisper.hotkey")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        import sys as _sys
        from pathlib import Path
        if _sys.platform == "win32":
            log_dir = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        else:
            log_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        log_dir = log_dir / "turbo-whisper"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(str(log_dir / "turbo-whisper.log"), encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        # Also print to console
        console = logging.StreamHandler()
        console.setLevel(logging.WARNING)
        logger.addHandler(console)
    return logger


logger = _setup_logger()


def is_wayland() -> bool:
    """Check if running on Wayland."""
    if os.environ.get("TURBO_WHISPER_USE_PORTAL") == "1":
        return os.environ.get("XDG_SESSION_TYPE") == "wayland"
    return False


def _format_hotkey_for_portal(hotkey_combo: list[str]) -> str:
    """Convert hotkey combo to portal format."""
    parts = []
    for key in hotkey_combo:
        key_lower = key.lower()
        if key_lower in ("ctrl", "ctrl_l", "ctrl_r"):
            parts.append("CTRL")
        elif key_lower in ("alt", "alt_l", "alt_r"):
            parts.append("ALT")
        elif key_lower in ("shift", "shift_l", "shift_r"):
            parts.append("SHIFT")
        elif key_lower in ("super", "cmd"):
            parts.append("SUPER")
        else:
            parts.append(key_lower)
    return "+".join(parts)


class PortalHotkeyManager:
    """Wayland hotkey manager using xdg-desktop-portal GlobalShortcuts."""

    def __init__(self, hotkey_combo: list[str], callback: Callable[[], None]):
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib

        self.callback = callback
        self.hotkey_combo = hotkey_combo
        self.hotkey_str = _format_hotkey_for_portal(hotkey_combo)
        self._running = False
        self._loop = None
        self._thread = None
        self._session = None

        DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SessionBus()
        self._portal = self._bus.get_object(
            "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"
        )
        self._GLib = GLib
        self._dbus = dbus

    def _on_activated(self, session_handle, shortcut_id, timestamp, options):
        if shortcut_id == "turbo-whisper-toggle":
            self.callback()

    def _on_session_created(self, response, results):
        if response != 0:
            print(f"Portal: Failed to create session (response={response})")
            return
        self._session = results.get("session_handle")
        if not self._session:
            print("Portal: No session handle in response")
            return
        self._bus.add_signal_receiver(
            self._on_activated, signal_name="Activated",
            dbus_interface="org.freedesktop.portal.GlobalShortcuts",
            bus_name="org.freedesktop.portal.Desktop", path=self._session,
        )
        shortcuts = [
            ("turbo-whisper-toggle", {
                "description": self._dbus.String("Toggle Turbo Whisper recording"),
                "preferred-trigger": self._dbus.String(self.hotkey_str),
            }),
        ]
        try:
            self._portal.BindShortcuts(
                self._session, shortcuts, "", {},
                dbus_interface="org.freedesktop.portal.GlobalShortcuts",
            )
        except Exception as e:
            print(f"Portal: Failed to bind shortcuts: {e}")

    def _run_loop(self):
        self._loop = self._GLib.MainLoop()
        self._loop.run()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        options = {
            "handle_token": self._dbus.String("turbo_whisper"),
            "session_handle_token": self._dbus.String("turbo_whisper_session"),
        }
        try:
            reply = self._portal.CreateSession(
                options, dbus_interface="org.freedesktop.portal.GlobalShortcuts"
            )
            self._bus.add_signal_receiver(
                self._on_session_created, signal_name="Response",
                dbus_interface="org.freedesktop.portal.Request",
                bus_name="org.freedesktop.portal.Desktop", path=reply,
            )
        except Exception as e:
            print(f"Portal: Failed to create session: {e}")
            self._running = False
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._loop:
            self._loop.quit()
            self._loop = None
        self._thread = None


class WinApiHotkeyManager:
    """Windows hotkey manager using RegisterHotKey WinAPI with double-tap detection.

    For modifier combos (Ctrl+Space, Alt+~): uses RegisterHotKey — fires callback immediately.
    For single-character hotkeys (~, `, ё, etc.): uses RegisterHotKey with a 350ms
    detection window. This allows:
      - Single press -> wait 350ms, then toggle recording
      - Double press (within 350ms) -> type the character once in the active window
      The 350ms delay only applies to single-character hotkeys; modifier combos fire instantly.

    Does NOT block any keyboard input — only the configured hotkey is intercepted.
    Runs the message pump on a background thread.
    """

    def __init__(self, hotkey_combo: list[str], callback: Callable[[], None]):
        self.callback = callback
        self._running = False
        self._thread = None
        self._hwnd = None
        self._hotkey_id = 1
        self._modifiers = 0
        self._vk = 0
        self._is_single_char = False

        # Double-tap detection state
        self._double_tap_ms = 350  # Time window for double-tap detection
        self._first_press_time = 0
        self._double_tap_timer = None
        self._shift_at_first_press = False  # Shift state captured at first press for double-tap

        # Parse hotkey combo
        self._parse_combo(hotkey_combo)

    def _parse_combo(self, combo: list[str]) -> None:
        """Parse hotkey combo into WinAPI modifiers + virtual key code.

        WinAPI modifiers:
            MOD_ALT     = 0x0001
            MOD_CONTROL = 0x0002
            MOD_SHIFT   = 0x0004
            MOD_WIN     = 0x0008
            MOD_NOREPEAT = 0x4000
        """
        MOD_ALT = 0x0001
        MOD_CONTROL = 0x0002
        MOD_SHIFT = 0x0004
        MOD_WIN = 0x0008
        MOD_NOREPEAT = 0x4000

        key_map = {
            "alt": MOD_ALT, "alt_l": MOD_ALT, "alt_r": MOD_ALT,
            "ctrl": MOD_CONTROL, "ctrl_l": MOD_CONTROL, "ctrl_r": MOD_CONTROL,
            "shift": MOD_SHIFT, "shift_l": MOD_SHIFT, "shift_r": MOD_SHIFT,
            "super": MOD_WIN, "cmd": MOD_WIN, "win": MOD_WIN,
        }

        modifiers = 0
        main_key = None

        for key_name in combo:
            key_lower = key_name.lower()
            if key_lower in key_map:
                modifiers |= key_map[key_lower]
            else:
                main_key = key_name

        if main_key is None:
            logger.warning(f"No main key in hotkey combo: {combo}, using '~'")
            main_key = "~"

        # Convert main key to virtual key code
        self._vk = self._char_to_vk(main_key)
        self._modifiers = modifiers  # Start without NOREPEAT

        # Detect if this is a single-character hotkey (no modifiers, one char key)
        self._is_single_char = (modifiers == 0) and (len(combo) == 1) and (len(main_key) == 1)

        # For modifier combos: add NOREPEAT (they fire once per press, no double-tap needed)
        # For single-char: NO NOREPEAT — we need to receive repeated WM_HOTKEY for double-tap detection
        if not self._is_single_char:
            self._modifiers |= MOD_NOREPEAT

        # Increase detection window for single-char hotkeys
        if self._is_single_char:
            self._double_tap_ms = 400  # Slightly longer for comfort

        logger.info(f"WinApiHotkeyManager: combo={combo}, mods=0x{self._modifiers:04x}, "
                    f"vk=0x{self._vk:02x}, single_char={self._is_single_char}")

    def _char_to_vk(self, char: str) -> int:
        """Convert a character to Windows virtual key code."""
        if len(char) == 1:
            c = char.lower()
            # Virtual key codes for common keys
            vk_map = {
                '`': 0xC0, '~': 0xC0, 'ё': 0xC0,  # All map to VK_OEM_3
                '-': 0xBD, '_': 0xBD, '=': 0xBB, '+': 0xBB,
                '[': 0xDB, '{': 0xDB, ']': 0xDD, '}': 0xDD,
                '\\': 0xDC, '|': 0xDC, ';': 0xBA, ':': 0xBA,
                "'": 0xDE, '"': 0xDE, ',': 0xBC, '<': 0xBC,
                '.': 0xBE, '>': 0xBE, '/': 0xBF, '?': 0xBF,
                ' ': 0x20, '\t': 0x09, '\n': 0x0D,
            }
            if c in vk_map:
                return vk_map[c]
            # Letters a-z -> VK_A (0x41) to VK_Z (0x5A)
            if 'a' <= c <= 'z':
                return ord(c.upper())
            # Numbers 0-9 -> VK_0 (0x30) to VK_9 (0x39)
            if '0' <= c <= '9':
                return ord(c)

        # Named keys
        named_map = {
            "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
            "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
            "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
            "enter": 0x0D, "return": 0x0D, "tab": 0x09,
            "space": 0x20, "esc": 0x1B, "escape": 0x1B,
            "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
            "home": 0x24, "end": 0x23, "insert": 0x2D, "ins": 0x2D,
            "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
            "pageup": 0x21, "pagedown": 0x22,
            "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
            "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
        }
        key_lower = char.lower()
        if key_lower in named_map:
            return named_map[key_lower]

        logger.warning(f"Unknown key '{char}', defaulting to VK_OEM_3 (~)")
        return 0xC0

    def _on_single_press_timeout(self) -> None:
        """Called when the double-tap detection timer expires (single press detected)."""
        self._first_press_time = 0
        self._double_tap_timer = None
        logger.info("Single press detected via double-tap timeout, triggering callback")
        self.callback()

    def _get_char_from_vk(self, vk_code: int, shift_pressed: bool = False) -> str:
        """Convert a virtual key code to the actual character using current keyboard layout.

        Uses ToUnicodeEx WinAPI which respects:
        - Current keyboard layout (layout-aware)
        - Shift state (via shift_pressed parameter or GetAsyncKeyState)
        - CapsLock state
        - Dead key sequences

        Rather than relying on GetKeyboardState (which may not reflect modifier
        state correctly when called from a background thread hook), we manually
        construct the keyboard state buffer with known modifier states.

        Args:
            vk_code: Windows virtual key code (e.g. 0xC0 for VK_OEM_3)
            shift_pressed: If True, synthesizes Shift state in the keyboard buffer

        Returns:
            Character string (e.g. '~', '`', 'ё', 'a', '1'), or empty string if no mapping.
        """
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # Get current keyboard layout for the foreground thread
        foreground_tid = user32.GetWindowThreadProcessId(
            user32.GetForegroundWindow(), None
        )
        layout = user32.GetKeyboardLayout(foreground_tid)

        # Manually build keyboard state buffer with known modifiers
        # This is more reliable than GetKeyboardState from a background thread
        keyboard_state = ctypes.create_string_buffer(256)
        ctypes.memset(keyboard_state, 0, 256)

        VK_SHIFT = 0x10
        VK_CAPITAL = 0x14

        # Check if Shift is pressed via GetAsyncKeyState (works from any thread)
        if shift_pressed or (user32.GetAsyncKeyState(VK_SHIFT) & 0x8000):
            keyboard_state[VK_SHIFT] = 0x81  # Key down + toggle state

        # Check CapsLock
        if user32.GetKeyState(VK_CAPITAL) & 0x01:
            keyboard_state[VK_CAPITAL] = 0x01  # Toggled on

        # Get scan code from virtual key
        scan_code = user32.MapVirtualKeyW(vk_code, 0)

        # Buffer for the resulting character (Unicode)
        char_buf = ctypes.create_unicode_buffer(8)

        # Convert VK to Unicode using ToUnicodeEx with flags=1
        # Flag 1 = do not modify dead key state (keeps keyboard state consistent)
        # This prevents ToUnicodeEx from consuming dead keys and affecting
        # subsequent keyboard input in the active window.
        result_len = user32.ToUnicodeEx(
            vk_code,
            scan_code,
            keyboard_state,
            char_buf,
            len(char_buf),
            1,  # flags=1: no dead key state modification
            layout,
        )

        if result_len > 0:
            return char_buf.value[0:result_len]

        # If result is 0 (dead key or no mapping), try once more with flags=0
        # to get the actual character (some layouts need this)
        result_len = user32.ToUnicodeEx(
            vk_code,
            scan_code,
            keyboard_state,
            char_buf,
            len(char_buf),
            0,  # flags=0: may modify dead key state
            layout,
        )

        if result_len > 0:
            return char_buf.value[0:result_len]

        return ""

    def _insert_char_via_clipboard(self, char: str) -> None:
        """Insert a single character into the active window using clipboard paste.

        Uses the same reliable mechanism as Typer._type_clipboard_paste:
        1. Copy character to clipboard via pyperclip or clip.exe
        2. Simulate Ctrl+V via keybd_event

        This bypasses UIPI and works cross-process.
        """
        import ctypes
        import time

        user32 = ctypes.windll.user32
        VK_CONTROL = 0x11
        VK_V = 0x56
        KEYEVENTF_KEYUP = 0x0002

        # Step 1: Copy char to clipboard
        try:
            import pyperclip
            pyperclip.copy(char)
        except Exception:
            try:
                import subprocess as sp
                import io
                proc = sp.Popen(["clip.exe"], stdin=sp.PIPE, shell=True)
                encoded = char.encode("utf-16-le") + b"\0\0"
                proc.communicate(input=encoded)
            except Exception:
                logger.error("_insert_char_via_clipboard: clipboard copy failed")
                return

        time.sleep(0.05)

        # Step 2: Paste via keybd_event (NOT blocked by UIPI)
        user32.keybd_event(VK_CONTROL, 0, 0, 0)        # Ctrl down
        time.sleep(0.02)
        user32.keybd_event(VK_V, 0, 0, 0)               # V down
        time.sleep(0.02)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0) # V up
        time.sleep(0.02)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)  # Ctrl up

        logger.info(f"_insert_char_via_clipboard: pasted char='{char}'")

    def _hook_worker(self) -> None:
        """Background thread for single-character hotkeys: use WH_KEYBOARD_LL hook
        with double-tap detection and CapsLock/Shift-aware character mapping.

        On first press → start detection timer, block the key.
        On second press within timeout → inject a single character, don't toggle.
        On timeout → call callback (toggle).

        Shift state is captured at the time of the first key press, so that when
        double-tap occurs (both presses blocked), the correct shifted character
        (e.g., '~' instead of '`') is determined based on what the user intended.
        """
        import ctypes
        import ctypes.wintypes
        import threading

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # Save thread ID for proper cleanup in stop()
        self._hook_thread_id = threading.current_thread().ident

        WH_KEYBOARD_LL = 13
        WM_KEYDOWN = 0x0100

        class KBDLLHOOKSTRUCT(ctypes.Structure):
            _fields_ = [
                ("vkCode", ctypes.c_uint32),
                ("scanCode", ctypes.c_uint32),
                ("flags", ctypes.c_uint32),
                ("time", ctypes.c_uint32),
                ("dwExtraInfo", ctypes.c_uint64),
            ]

        HOOKPROC = ctypes.WINFUNCTYPE(
            ctypes.c_int64,     # LRESULT (64-bit signed)
            ctypes.c_int,       # int nCode (32-bit)
            ctypes.c_void_p,    # WPARAM
            ctypes.c_void_p,    # LPARAM
        )

        def hook_proc(nCode, wParam, lParam):
            if nCode >= 0 and wParam == WM_KEYDOWN:
                kb = KBDLLHOOKSTRUCT.from_address(lParam)
                if kb.vkCode == self._vk:
                    VK_SHIFT = 0x10
                    VK_CONTROL = 0x11
                    VK_MENU = 0x12  # Alt
                    VK_LWIN = 0x5B
                    VK_RWIN = 0x5C

                    # Check if Ctrl/Alt/Win is held — pass through, don't interfere
                    if (user32.GetAsyncKeyState(VK_CONTROL) & 0x8000 or
                        user32.GetAsyncKeyState(VK_MENU) & 0x8000 or
                        user32.GetAsyncKeyState(VK_LWIN) & 0x8000 or
                        user32.GetAsyncKeyState(VK_RWIN) & 0x8000):
                        return user32.CallNextHookEx(0, nCode, wParam, lParam)

                    # Save Shift state at the time of this key press for double-tap detection
                    shift_held = bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)

                    now = time.time() * 1000

                    if self._first_press_time == 0:
                        # First press: save shift state and start detection timer
                        self._first_press_time = now
                        self._shift_at_first_press = shift_held
                        self._double_tap_timer = threading.Timer(
                            self._double_tap_ms / 1000.0,
                            self._on_single_press_timeout,
                        )
                        self._double_tap_timer.daemon = True
                        self._double_tap_timer.start()
                        logger.debug(f"Hook: first press, timer started (shift={shift_held})")
                        # Block this key press
                        return 1
                    else:
                        # Second press within timeout window
                        elapsed = now - self._first_press_time
                        if elapsed < self._double_tap_ms:
                            # Double-tap detected: cancel timer and inject character
                            if self._double_tap_timer:
                                self._double_tap_timer.cancel()
                                self._double_tap_timer = None
                            self._first_press_time = 0

                            # Use Shift state from first press for correct character case
                            char = self._get_char_from_vk(self._vk, shift_pressed=self._shift_at_first_press)
                            if char:
                                self._insert_char_via_clipboard(char)
                                logger.info(f"Hook: Double-tap detected, pasted char='{char}' (shift={self._shift_at_first_press})")
                            else:
                                logger.warning(f"Hook: Double-tap detected but no char mapping for vk=0x{self._vk:02x}")

                            # Block this press too (we already inserted the character)
                            return 1
                        else:
                            # Past timer window: reset
                            self._first_press_time = 0

            return user32.CallNextHookEx(0, nCode, wParam, lParam)

        # Set proper argtypes for CallNextHookEx to match HOOKPROC signatures
        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p,  # HHOOK
            ctypes.c_int,     # int nCode
            ctypes.c_void_p,  # WPARAM (matches HOOKPROC)
            ctypes.c_void_p,  # LPARAM (matches HOOKPROC)
        ]
        user32.CallNextHookEx.restype = ctypes.c_int64  # LRESULT

        # Install WH_KEYBOARD_LL hook
        # MSDN: For WH_KEYBOARD_LL, hMod MUST be NULL (0) because the hook procedure
        # is in the module mapped into the current process. Using GetModuleHandleW
        # causes ERROR_MOD_NOT_FOUND (126) on most systems.
        self._hook_proc = HOOKPROC(hook_proc)
        hook_id = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._hook_proc,
            0,  # NULL — correct for WH_KEYBOARD_LL per MSDN
            0,  # dwThreadId=0 -> global hook
        )

        if not hook_id:
            error_code = ctypes.windll.kernel32.GetLastError()
            logger.error(f"WinAPI: Failed to install WH_KEYBOARD_LL hook (error={error_code})")
            logger.warning("Falling back to RegisterHotKey (no double-tap)")
            return

        self._hook_id = hook_id
        logger.info(f"WinAPI: WH_KEYBOARD_LL hook installed (id={hook_id})")

        # Message loop (required for low-level hooks to work)
        msg = ctypes.wintypes.MSG()
        while self._running:
            ret = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
            if ret <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        # Cleanup
        user32.UnhookWindowsHookEx(hook_id)
        self._hook_id = None
        logger.info("WinAPI: WH_KEYBOARD_LL hook removed")

    def _register_hotkey_worker(self) -> None:
        """Background thread: use RegisterHotKey.

        For modifier combos: fires callback immediately.
        For single-character hotkeys: uses timer-based double-tap detection:
          - First WM_HOTKEY -> start 350ms timer
          - Second WM_HOTKEY within 350ms -> call _simulate_single_keypress (don't toggle)
          - Timer expires -> call callback (toggle recording)
        """
        import ctypes
        import ctypes.wintypes

        user32 = ctypes.windll.user32

        user32.DefWindowProcW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint64, ctypes.c_int64,
        ]
        user32.DefWindowProcW.restype = ctypes.c_int64

        user32.RegisterClassW.argtypes = [ctypes.c_void_p]
        user32.RegisterClassW.restype = ctypes.c_uint16

        user32.CreateWindowExW.argtypes = [
            ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p,
            ctypes.c_uint32, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]
        user32.CreateWindowExW.restype = ctypes.c_void_p

        user32.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32, ctypes.c_uint32]
        user32.RegisterHotKey.restype = ctypes.c_int
        user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.UnregisterHotKey.restype = ctypes.c_int
        user32.DestroyWindow.argtypes = [ctypes.c_void_p]
        user32.DestroyWindow.restype = ctypes.c_int
        user32.GetMessageW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = [ctypes.c_void_p]
        user32.TranslateMessage.restype = ctypes.c_int
        user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
        user32.DispatchMessageW.restype = ctypes.c_int

        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_int64, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint64, ctypes.c_int64)

        class_name = "TurboWhisperHotkeyWindow"
        hinstance = ctypes.windll.kernel32.GetModuleHandleW(None)

        WM_HOTKEY = 0x0312

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_HOTKEY:
                now = time.time() * 1000

                if self._is_single_char:
                    # For single-character hotkeys, check if any modifier is held.
                    # RegisterHotKey for a plain key (no modifiers) fires even when
                    # Ctrl/Alt/Shift is pressed — we must ignore those cases and let
                    # the key combination pass through to the active window.
                    VK_SHIFT = 0x10
                    VK_CONTROL = 0x11
                    VK_MENU = 0x12  # Alt
                    VK_LWIN = 0x5B
                    VK_RWIN = 0x5C

                    if (user32.GetAsyncKeyState(VK_CONTROL) & 0x8000 or
                        user32.GetAsyncKeyState(VK_MENU) & 0x8000 or
                        user32.GetAsyncKeyState(VK_SHIFT) & 0x8000 or
                        user32.GetAsyncKeyState(VK_LWIN) & 0x8000 or
                        user32.GetAsyncKeyState(VK_RWIN) & 0x8000):
                        # Modifier is held — this is a keyboard shortcut, not our hotkey.
                        # Let the key pass through to the active window.
                        # We need to inject the actual character via keybd_event so the
                        # window receives the expected input.
                        char = self._get_char_from_vk(self._vk)
                        if char:
                            self._insert_char_via_clipboard(char)
                            logger.info(f"WinAPI: Modifier+single-char detected, injected char='{char}'")
                        return 0

                    # Ignore auto-repeat (WM_HOTKEY without key release)
                    # If a timer is already running and the press is too fast (<80ms),
                    # it's likely auto-repeat from holding the key, not a real double-tap
                    if self._first_press_time > 0:
                        elapsed = now - self._first_press_time
                        if elapsed < 80:
                            # Auto-repeat: ignore
                            return 0
                        if elapsed < self._double_tap_ms:
                            # Double-tap detected! Cancel timer and inject character
                            if self._double_tap_timer:
                                self._double_tap_timer.cancel()
                                self._double_tap_timer = None
                            self._first_press_time = 0
                            # Get the actual character from current keyboard layout
                            char = self._get_char_from_vk(self._vk)
                            if char:
                                self._insert_char_via_clipboard(char)
                                logger.info(f"WinAPI: Double-tap detected, pasted char='{char}'")
                            else:
                                logger.warning(f"WinAPI: Double-tap detected but no char mapping for vk=0x{self._vk:02x}")
                            return 0
                        else:
                            # Past timer window: reset
                            self._first_press_time = 0

                    # First press (or reset): start detection timer
                    self._first_press_time = now
                    self._double_tap_timer = threading.Timer(
                        self._double_tap_ms / 1000.0,
                        self._on_single_press_timeout,
                    )
                    self._double_tap_timer.daemon = True
                    self._double_tap_timer.start()
                    logger.info("WinAPI: Single-char hotkey first press, timer started")
                else:
                    # Modifier combo: fire immediately
                    self.callback()
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        WNDCLASS = ctypes.wintypes.WNDCLASSW if hasattr(ctypes.wintypes, 'WNDCLASSW') else None
        if WNDCLASS is None:
            class WNDCLASS(ctypes.Structure):
                _fields_ = [
                    ("style", ctypes.c_uint),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", ctypes.c_void_p),
                    ("hIcon", ctypes.c_void_p),
                    ("hCursor", ctypes.c_void_p),
                    ("hbrBackground", ctypes.c_void_p),
                    ("lpszMenuName", ctypes.c_wchar_p),
                    ("lpszClassName", ctypes.c_wchar_p),
                ]

        proc = WNDPROC(wndproc)
        wc = WNDCLASS()
        wc.style = 0
        wc.lpfnWndProc = proc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinstance
        wc.hIcon = 0
        wc.hCursor = 0
        wc.hbrBackground = 0
        wc.lpszMenuName = None
        wc.lpszClassName = class_name

        atom = user32.RegisterClassW(ctypes.byref(wc))
        if atom == 0:
            logger.error("WinAPI: Failed to register window class")
            return

        hwnd = user32.CreateWindowExW(0, class_name, "TurboWhisperHotkeyWindow", 0, 0, 0, 0, 0, 0, 0, hinstance, 0)
        if not hwnd:
            logger.error("WinAPI: Failed to create window")
            return

        self._hwnd = ctypes.c_void_p(hwnd)

        result = user32.RegisterHotKey(hwnd, self._hotkey_id, self._modifiers, self._vk)
        if result == 0:
            logger.error(f"WinAPI: RegisterHotKey failed (mods=0x{self._modifiers:04x}, vk=0x{self._vk:02x})")
            user32.DestroyWindow(hwnd)
            return

        logger.info(f"WinAPI: RegisterHotKey registered (id={self._hotkey_id})")

        msg = ctypes.wintypes.MSG()
        while self._running:
            ret = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
            if ret == 0:
                break
            if ret == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        user32.UnregisterHotKey(hwnd, self._hotkey_id)
        user32.DestroyWindow(hwnd)
        user32.UnregisterClassW(class_name, hinstance)
        logger.info("WinAPI: RegisterHotKey worker exited")

    def _winapi_worker(self) -> None:
        """Background thread entry point.

        For single-character hotkeys: tries WH_KEYBOARD_LL hook first for proper
        double-tap detection with key suppression. Falls back to RegisterHotKey
        (without NOREPEAT for double-tap detection via repeated WM_HOTKEY, with
        auto-repeat filtering via <80ms threshold).
        For modifier combos: RegisterHotKey WITH MOD_NOREPEAT for instant firing.
        """
        if self._is_single_char:
            self._hook_worker()
            if not hasattr(self, '_hook_id') or not self._hook_id:
                logger.warning("WH_KEYBOARD_LL hook failed, falling back to RegisterHotKey (no double-tap)")
                self._register_hotkey_worker()
        else:
            self._register_hotkey_worker()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._hook_thread_id = None  # Will be set by the worker if it's a hook-based hotkey
        self._thread = threading.Thread(target=self._winapi_worker, daemon=True)
        self._thread.start()
        logger.info("WinAPI: Hotkey manager started (RegisterHotKey)")

    def stop(self) -> None:
        self._running = False
        # Cancel any pending double-tap timer
        if self._double_tap_timer:
            self._double_tap_timer.cancel()
            self._double_tap_timer = None
        self._first_press_time = 0

        if self._hwnd:
            import ctypes
            user32 = ctypes.windll.user32
            user32.PostMessageW(self._hwnd, 0x0012, 0, 0)  # WM_QUIT
            self._hwnd = None

        # Wake up the hook worker thread if it exists
        if self._hook_thread_id:
            try:
                import ctypes
                user32 = ctypes.windll.user32
                user32.PostThreadMessageW(self._hook_thread_id, 0x0012, 0, 0)  # WM_QUIT
            except Exception:
                pass
            self._hook_thread_id = None

        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None
        logger.info("WinAPI: Hotkey manager stopped")


class HotkeyManager:
    """Manages global hotkey registration using pynput (X11/macOS only).

    On Windows, WinApiHotkeyManager is used instead to avoid blocking all keyboard input.
    Supports single-character hotkeys like '~' with key suppression (non-Windows).
    """

    def __init__(self, hotkey_combo: list[str], callback: Callable[[], None]):
        from pynput import keyboard

        self._keyboard = keyboard
        self.callback = callback
        self.hotkey_combo = self._parse_hotkey(hotkey_combo)
        self.hotkey_chars = self._get_char_keys(hotkey_combo)
        self.current_keys = set()
        self.current_chars = set()
        self.listener = None
        self._running = False
        self._last_trigger = 0
        self._debounce_ms = 300

        # Double-tap detection
        self._double_tap_ms = 400
        self._single_tap_timer = None
        self._pending_single_tap = False
        self._first_press_time = 0

        # Suppression for single-character hotkeys
        self._suppress_on_match = any(len(k) == 1 for k in hotkey_combo)

        # Normalize hotkey chars to lowercase for matching
        self._hotkey_single_chars_lower = {c.lower() for c in hotkey_combo if len(c) == 1}

        # Check if this is a grave/tilde/backtick key hotkey (physical key above Tab)
        self._grave_aliases = {"`", "~", "ё"}
        self._is_grave_hotkey = bool(self._hotkey_single_chars_lower & self._grave_aliases)

        logger.info(f"HotkeyManager initialized: combo={hotkey_combo}, "
                    f"chars={self._hotkey_single_chars_lower}, "
                    f"grave_hotkey={self._is_grave_hotkey}, "
                    f"suppress={self._suppress_on_match}")

    def _get_char_keys(self, combo: list[str]) -> set:
        return {k.lower() for k in combo if len(k) == 1}

    def _parse_hotkey(self, combo: list[str]) -> set:
        kb = self._keyboard
        key_map = {
            "alt": kb.Key.alt, "alt_l": kb.Key.alt_l, "alt_r": kb.Key.alt_r,
            "ctrl": kb.Key.ctrl, "ctrl_l": kb.Key.ctrl_l, "ctrl_r": kb.Key.ctrl_r,
            "shift": kb.Key.shift, "shift_l": kb.Key.shift_l, "shift_r": kb.Key.shift_r,
            "cmd": kb.Key.cmd, "super": kb.Key.cmd,
            "space": kb.Key.space, "tab": kb.Key.tab, "enter": kb.Key.enter,
            "esc": kb.Key.esc, "backspace": kb.Key.backspace,
            "f1": kb.Key.f1, "f2": kb.Key.f2, "f3": kb.Key.f3,
            "f4": kb.Key.f4, "f5": kb.Key.f5, "f6": kb.Key.f6,
            "f7": kb.Key.f7, "f8": kb.Key.f8, "f9": kb.Key.f9,
            "f10": kb.Key.f10, "f11": kb.Key.f11, "f12": kb.Key.f12,
        }
        parsed = set()
        for key_name in combo:
            key_lower = key_name.lower()
            if key_lower in key_map:
                parsed.add(key_map[key_lower])
            elif len(key_lower) == 1:
                parsed.add(kb.KeyCode.from_char(key_lower))
            else:
                logger.warning(f"Unknown key '{key_name}'")
        return parsed

    def _char_matches_hotkey(self, char_lower: str) -> bool:
        """Check if a character from a key press matches the configured hotkey.

        For grave/tilde/backtick (~ ` ё) — they are the same physical key on all keyboards,
        so we treat them as interchangeable when the hotkey is any of them.
        """
        if char_lower in self._hotkey_single_chars_lower:
            return True
        # Grave key flexibility: if ~, `, or ё is configured, any of them matches
        if self._is_grave_hotkey and char_lower in self._grave_aliases:
            return True
        return False

    def _cancel_single_tap_timer(self) -> None:
        if self._single_tap_timer:
            self._single_tap_timer.cancel()
            self._single_tap_timer = None
        self._pending_single_tap = False

    def _on_single_tap_timeout(self) -> None:
        logger.info("Single-tap timeout fired — triggering callback")
        self._pending_single_tap = False
        self.callback()

    def _on_press(self, key) -> bool | None:
        """Handle key press event.

        Returns:
            False to suppress, True to allow, None for default
        """
        kb = self._keyboard
        key_log = f"key={key}"

        # Extract character info
        char_lower = ""
        if hasattr(key, "char") and key.char:
            char_lower = key.char.lower()
            key_log += f", char='{key.char}'"
        if hasattr(key, "vk"):
            key_log += f", vk={key.vk}"
        if hasattr(key, "scan"):
            key_log += f", scan={key.scan}"

        logger.debug(f"on_press: {key_log}")

        # Track character keys separately
        if hasattr(key, "char") and key.char:
            self.current_chars.add(key.char.lower())

        # Track special keys (non-character)
        if not hasattr(key, "char") or not key.char:
            self.current_keys.add(key)

        # Check for alt/ctrl/shift variants
        if key in (kb.Key.alt_l, kb.Key.alt_r):
            self.current_keys.add(kb.Key.alt)
        if key in (kb.Key.ctrl_l, kb.Key.ctrl_r):
            self.current_keys.add(kb.Key.ctrl)
        if key in (kb.Key.shift_l, kb.Key.shift_r):
            self.current_keys.add(kb.Key.shift)

        # Grave key cross-layout: if ~ is configured and we get ё (or vice versa),
        # match them as the same physical key
        if self._is_grave_hotkey and char_lower in self._grave_aliases:
            # Add all grave variants so any of them matches
            for alias in self._grave_aliases:
                self.current_chars.add(alias)
            logger.debug(f"Grave key detected: '{key.char}' added all aliases")

        # Check if hotkey combo is pressed
        # For single-character hotkeys (~, `, etc.), only check char_keys_match
        # because the key is stored in current_chars, not current_keys.
        # For modifier combos (alt+space, etc.), check both.
        if self._suppress_on_match:
            # Single-character hotkey: only check character match
            combo_matched = self.hotkey_chars.issubset(self.current_chars)
        else:
            # Modifier combo: check both special keys and character keys
            special_keys_match = self.hotkey_combo.issubset(self.current_keys)
            char_keys_match = self.hotkey_chars.issubset(self.current_chars)
            combo_matched = special_keys_match and char_keys_match

        logger.debug(f"  Match check: combo_matched={combo_matched}, "
                     f"current_chars={self.current_chars}, wanted={self.hotkey_chars}")

        if combo_matched:
            now = time.time() * 1000

            # --- SINGLE-CHARACTER HOTKEY (suppress mode) ---
            if self._suppress_on_match:
                # Trigger immediately: suppress character and call callback
                logger.info("Hotkey triggered: suppressing character, calling callback")
                self._last_trigger = now
                self.current_keys.clear()
                self.current_chars.clear()
                # Direct call: callback goes through pyqtSignal.emit() which is
                # thread-safe in PyQt6 — it posts the call to the Qt main thread.
                self.callback()
                # NOTE: Do NOT return False here! In pynput, returning False from
                # on_press stops the entire listener. We rely on suppress=True on
                # the Listener to suppress the key at the OS level, and return None
                # to keep the listener alive for subsequent key presses.
                return None

            # --- LEGACY: modifier combos (no suppress) ---
            if now - self._last_trigger > self._debounce_ms:
                self._last_trigger = now
                self.current_keys.clear()
                self.current_chars.clear()
                self.callback()
                return None

        # Allow non-matching keys through (only if suppression is active)
        if self._suppress_on_match:
            return True

        return None

    def _on_release(self, key) -> None:
        kb = self._keyboard
        if hasattr(key, "char") and key.char:
            self.current_chars.discard(key.char.lower())
        else:
            self.current_keys.discard(key)
        if key in (kb.Key.alt_l, kb.Key.alt_r):
            self.current_keys.discard(kb.Key.alt)
        if key in (kb.Key.ctrl_l, kb.Key.ctrl_r):
            self.current_keys.discard(kb.Key.ctrl)
        if key in (kb.Key.shift_l, kb.Key.shift_r):
            self.current_keys.discard(kb.Key.shift)
        # Clear grave aliases on any key release
        if self._is_grave_hotkey:
            self.current_chars.difference_update(self._grave_aliases)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        logger.info(f"Starting hotkey listener, suppress={self._suppress_on_match}")
        self.listener = self._keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release,
            suppress=self._suppress_on_match,
        )
        self.listener.start()
        logger.info("Hotkey listener started")

    def stop(self) -> None:
        self._running = False
        self._cancel_single_tap_timer()
        if self.listener:
            self.listener.stop()
            self.listener = None
        logger.info("Hotkey listener stopped")


def create_hotkey_manager(
    hotkey_combo: list[str], callback: Callable[[], None]
) -> WinApiHotkeyManager | HotkeyManager | PortalHotkeyManager | None:
    """Create the appropriate hotkey manager for the current platform.

    On Windows: uses WinApiHotkeyManager (RegisterHotKey) — does NOT block other keys.
    On Linux/Wayland: uses PortalHotkeyManager.
    On Linux/X11 or macOS: uses HotkeyManager (pynput).
    """
    if sys.platform == "win32":
        # Windows: use WinAPI RegisterHotKey — does not block keyboard
        try:
            manager = WinApiHotkeyManager(hotkey_combo, callback)
            return manager
        except Exception as e:
            logger.warning(f"WinAPI hotkeys unavailable: {e}")
            # Fallback to pynput on Windows (with suppress=False to not block everything)
            try:
                manager = HotkeyManager(hotkey_combo, callback)
                # Override suppress to False on Windows — we don't want to block all keys
                manager._suppress_on_match = False
                logger.warning("Falling back to pynput with suppress=False")
                return manager
            except Exception as e2:
                logger.warning(f"pynput fallback also failed: {e2}")
                return None

    if is_wayland():
        try:
            manager = PortalHotkeyManager(hotkey_combo, callback)
            return manager
        except Exception as e:
            logger.warning(f"Portal hotkeys unavailable: {e}")
            return None
    else:
        return HotkeyManager(hotkey_combo, callback)
