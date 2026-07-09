"""Auto-type functionality - cross-platform using clipboard paste (Ctrl+V/Cmd+V) or character typing."""

import logging
import platform
import shutil
import subprocess
import time

SYSTEM = platform.system()

# Setup logger
logger = logging.getLogger("turbo-whisper.typer")
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    import sys as _sys
    from pathlib import Path
    if _sys.platform == "win32":
        log_dir = Path(__import__("os").environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        log_dir = Path(__import__("os").environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    log_dir = log_dir / "turbo-whisper"
    log_dir.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(str(log_dir / "turbo-whisper.log"), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] typer: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(fmt)
    logger.addHandler(ch)


class Typer:
    """Types text into the currently focused window.

    Default behavior: copy to clipboard and simulate Ctrl+V/Cmd+V (paste).
    Falls back to character-by-character typing if use_character_typing=True.
    """

    def __init__(self, typing_delay_ms: int = 5, use_character_typing: bool = False, copy_to_clipboard: bool = True):
        self.system = SYSTEM
        self._uinput = None
        self._evdev_available = False
        self._typing_delay = typing_delay_ms / 1000.0  # Convert to seconds
        self._use_character_typing = use_character_typing
        self._copy_to_clipboard = copy_to_clipboard  # Whether to keep text in clipboard after paste
        # Store target hwnd captured before recording starts
        self._target_hwnd = 0
        self._target_title = ""
        self._is_console = False  # True if target is CMD/PowerShell
        # Saved child edit/Scintilla control handle for direct WM_PASTE
        self._target_edit_hwnd = 0

        if self.system == "Linux":
            self._setup_linux()

    def _setup_linux(self) -> None:
        """Set up Linux typing backend (evdev for uinput access)."""
        try:
            from evdev import UInput, ecodes

            # Key mapping: character -> (keycode, needs_shift)
            self._key_map = self._build_key_map(ecodes)
            self._ecodes = ecodes

            # Try to create UInput device
            cap = {ecodes.EV_KEY: list(range(1, 128))}
            self._uinput = UInput(cap, name="turbo-whisper-keyboard")
            self._evdev_available = True
        except PermissionError:
            print("evdev: Permission denied for /dev/uinput")
            print("Fix with: sudo usermod -aG input $USER (then log out/in)")
            self._evdev_available = False
        except Exception as e:
            print(f"evdev unavailable: {e}")
            self._evdev_available = False

    def _build_key_map(self, ecodes) -> dict:
        """Build character to keycode mapping."""
        # US QWERTY layout
        key_map = {
            # Letters (lowercase - no shift)
            "a": (ecodes.KEY_A, False),
            "b": (ecodes.KEY_B, False),
            "c": (ecodes.KEY_C, False),
            "d": (ecodes.KEY_D, False),
            "e": (ecodes.KEY_E, False),
            "f": (ecodes.KEY_F, False),
            "g": (ecodes.KEY_G, False),
            "h": (ecodes.KEY_H, False),
            "i": (ecodes.KEY_I, False),
            "j": (ecodes.KEY_J, False),
            "k": (ecodes.KEY_K, False),
            "l": (ecodes.KEY_L, False),
            "m": (ecodes.KEY_M, False),
            "n": (ecodes.KEY_N, False),
            "o": (ecodes.KEY_O, False),
            "p": (ecodes.KEY_P, False),
            "q": (ecodes.KEY_Q, False),
            "r": (ecodes.KEY_R, False),
            "s": (ecodes.KEY_S, False),
            "t": (ecodes.KEY_T, False),
            "u": (ecodes.KEY_U, False),
            "v": (ecodes.KEY_V, False),
            "w": (ecodes.KEY_W, False),
            "x": (ecodes.KEY_X, False),
            "y": (ecodes.KEY_Y, False),
            "z": (ecodes.KEY_Z, False),
            # Letters (uppercase - with shift)
            "A": (ecodes.KEY_A, True),
            "B": (ecodes.KEY_B, True),
            "C": (ecodes.KEY_C, True),
            "D": (ecodes.KEY_D, True),
            "E": (ecodes.KEY_E, True),
            "F": (ecodes.KEY_F, True),
            "G": (ecodes.KEY_G, True),
            "H": (ecodes.KEY_H, True),
            "I": (ecodes.KEY_I, True),
            "J": (ecodes.KEY_J, True),
            "K": (ecodes.KEY_K, True),
            "L": (ecodes.KEY_L, True),
            "M": (ecodes.KEY_M, True),
            "N": (ecodes.KEY_N, True),
            "O": (ecodes.KEY_O, True),
            "P": (ecodes.KEY_P, True),
            "Q": (ecodes.KEY_Q, True),
            "R": (ecodes.KEY_R, True),
            "S": (ecodes.KEY_S, True),
            "T": (ecodes.KEY_T, True),
            "U": (ecodes.KEY_U, True),
            "V": (ecodes.KEY_V, True),
            "W": (ecodes.KEY_W, True),
            "X": (ecodes.KEY_X, True),
            "Y": (ecodes.KEY_Y, True),
            "Z": (ecodes.KEY_Z, True),
            # Numbers
            "1": (ecodes.KEY_1, False),
            "2": (ecodes.KEY_2, False),
            "3": (ecodes.KEY_3, False),
            "4": (ecodes.KEY_4, False),
            "5": (ecodes.KEY_5, False),
            "6": (ecodes.KEY_6, False),
            "7": (ecodes.KEY_7, False),
            "8": (ecodes.KEY_8, False),
            "9": (ecodes.KEY_9, False),
            "0": (ecodes.KEY_0, False),
            # Shifted numbers (symbols)
            "!": (ecodes.KEY_1, True),
            "@": (ecodes.KEY_2, True),
            "#": (ecodes.KEY_3, True),
            "$": (ecodes.KEY_4, True),
            "%": (ecodes.KEY_5, True),
            "^": (ecodes.KEY_6, True),
            "&": (ecodes.KEY_7, True),
            "*": (ecodes.KEY_8, True),
            "(": (ecodes.KEY_9, True),
            ")": (ecodes.KEY_0, True),
            # Punctuation
            " ": (ecodes.KEY_SPACE, False),
            "\n": (ecodes.KEY_ENTER, False),
            "\t": (ecodes.KEY_TAB, False),
            "-": (ecodes.KEY_MINUS, False),
            "_": (ecodes.KEY_MINUS, True),
            "=": (ecodes.KEY_EQUAL, False),
            "+": (ecodes.KEY_EQUAL, True),
            "[": (ecodes.KEY_LEFTBRACE, False),
            "{": (ecodes.KEY_LEFTBRACE, True),
            "]": (ecodes.KEY_RIGHTBRACE, False),
            "}": (ecodes.KEY_RIGHTBRACE, True),
            "\\": (ecodes.KEY_BACKSLASH, False),
            "|": (ecodes.KEY_BACKSLASH, True),
            ";": (ecodes.KEY_SEMICOLON, False),
            ":": (ecodes.KEY_SEMICOLON, True),
            "'": (ecodes.KEY_APOSTROPHE, False),
            '"': (ecodes.KEY_APOSTROPHE, True),
            ",": (ecodes.KEY_COMMA, False),
            "<": (ecodes.KEY_COMMA, True),
            ".": (ecodes.KEY_DOT, False),
            ">": (ecodes.KEY_DOT, True),
            "/": (ecodes.KEY_SLASH, False),
            "?": (ecodes.KEY_SLASH, True),
            "`": (ecodes.KEY_GRAVE, False),
            "~": (ecodes.KEY_GRAVE, True),
        }
        return key_map

    def type_text(self, text: str) -> bool:
        """
        Type text into the currently focused window.

        Uses clipboard paste (Ctrl+V/Cmd+V) by default for full Unicode support.
        Falls back to character-by-character typing if use_character_typing=True.

        Args:
            text: Text to type

        Returns:
            True if successful, False otherwise
        """
        if not text:
            logger.warning("type_text: empty text, returning False")
            return False

        logger.info(f"type_text START: text='{text[:80]}' (len={len(text)}), use_character_typing={self._use_character_typing}")

        if self._use_character_typing:
            result = self._type_characters(text)
            logger.info(f"type_text END: _type_characters returned {result}")
            return result
        else:
            result = self._type_clipboard_paste(text)
            logger.info(f"type_text END: _type_clipboard_paste returned {result}")
            return result

    def _type_clipboard_paste(self, text: str) -> bool:
        """
        Type text by copying to clipboard and simulating paste (Ctrl+V/Cmd+V).

        This method supports full Unicode (Russian, emoji, etc.) and is much faster
        than character-by-character typing.

        If copy_to_clipboard is False, the original clipboard content will be restored
        after pasting.

        Args:
            text: Text to paste

        Returns:
            True if successful, False otherwise
        """
        # Save original clipboard content if we need to restore it later
        saved_clipboard = ""
        if not self._copy_to_clipboard:
            saved_clipboard = self.get_clipboard_text()
            logger.info(f"_type_clipboard_paste: saved clipboard content ({len(saved_clipboard)} chars) for later restoration")

        # Copy text to clipboard
        logger.info(f"_type_clipboard_paste: copying '{text[:50]}...' to clipboard")
        clipboard_ok = self.copy_to_clipboard(text)
        logger.info(f"_type_clipboard_paste: copy_to_clipboard returned {clipboard_ok}")

        if not clipboard_ok:
            # Fallback to character typing if clipboard fails
            logger.warning("_type_clipboard_paste: clipboard copy failed, falling back to character typing")
            result = self._type_characters(text)
            logger.info(f"_type_clipboard_paste: fallback _type_characters returned {result}")
            return result

        # Delay for clipboard to settle and window focus to restore
        logger.info("_type_clipboard_paste: sleeping 0.15s for clipboard/window focus")
        time.sleep(0.15)

        # Perform paste operation
        result = False
        if self.system == "Windows":
            # Check if target is a console window
            if hasattr(self, '_is_console') and self._is_console:
                logger.info("_type_clipboard_paste: console window detected, using WM_CHAR method")
                result = self._paste_to_console(text)
            else:
                result = self._simulate_paste_windows()
            logger.info(f"_type_clipboard_paste: Windows paste -> {result}")
        elif self.system == "Darwin":
            result = self._simulate_paste_macos()
            logger.info(f"_type_clipboard_paste: macOS paste -> {result}")
        else:
            result = self._simulate_paste_linux()
            logger.info(f"_type_clipboard_paste: Linux paste -> {result}")

        # Restore original clipboard if copy_to_clipboard is False
        if not self._copy_to_clipboard and result:
            # Wait a bit to ensure paste operation completed
            time.sleep(0.2)
            restore_ok = self.copy_to_clipboard(saved_clipboard)
            logger.info(f"_type_clipboard_paste: restored original clipboard content -> {restore_ok}")

        return result

    def set_target_window(self, hwnd: int) -> None:
        """Set the target window handle for paste operations.

        This should be called BEFORE recording starts to capture the
        foreground window handle, so we can paste into it after recording.
        Also saves the first Edit/Scintilla child for direct WM_PASTE.
        """
        if hwnd:
            self._target_hwnd = hwnd
            self._is_console = False
            try:
                import ctypes
                user32 = ctypes.windll.user32
                length = user32.GetWindowTextLengthW(hwnd) + 1
                buf = ctypes.create_unicode_buffer(length)
                user32.GetWindowTextW(hwnd, buf, length)
                self._target_title = buf.value
                logger.info(f"set_target_window: saved hwnd={hwnd}, title='{self._target_title[:60]}'")

                # Detect console windows (CMD, PowerShell, Windows Terminal)
                class_buf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_buf, 256)
                class_name = class_buf.value
                if class_name in ('ConsoleWindowClass', 'PuTTY', 'mintty', 'Windows Terminal'):
                    self._is_console = True
                    logger.info(f"set_target_window: detected console window (class={class_name})")
            except Exception as e:
                logger.info(f"set_target_window: saved hwnd={hwnd}")

            # Also find and save the deepest Edit/Scintilla child for direct WM_PASTE
            try:
                import ctypes
                user32 = ctypes.windll.user32
                self._target_edit_hwnd = self._find_edit_control(hwnd, user32)
                if self._target_edit_hwnd:
                    logger.info(f"set_target_window: saved edit child hwnd={self._target_edit_hwnd}")
            except Exception as e:
                logger.warning(f"set_target_window: failed to find edit child: {e}")

    def _find_edit_control(self, parent_hwnd, user32) -> int:
        """Recursively find an Edit/Scintilla/RichEdit child control."""
        import ctypes

        child = user32.GetWindow(parent_hwnd, 5)  # GW_CHILD
        while child:
            buffer = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(child, buffer, 128)
            class_name = buffer.value
            if class_name in ('Edit', 'RichEdit20W', 'RichEdit50W', 'TextBox',
                              'RICHEDIT', 'RICHEDIT50W', 'WindowsForms10.EDIT.app.0.378734a',
                              'Scintilla'):
                return child
            # Recurse into children
            deeper = self._find_edit_control(child, user32)
            if deeper:
                return deeper
            child = user32.GetWindow(child, 2)  # GW_HWNDNEXT
        return 0

    def _simulate_paste_windows(self) -> bool:
        """Simulate paste on Windows using AttachThreadInput + keybd_event.

        Strategy:
        1. Attach to target window's input thread (bypasses focus stealing restrictions)
        2. SetForegroundWindow/BringWindowToTop on target
        3. keybd_event Ctrl+V (NOT blocked by UIPI, unlike SendInput)
        4. Restore focus to original window (if needed)
        
        This is the most reliable approach because:
        - keybd_event is a legacy API that is NOT subject to UIPI
        - AttachThreadInput allows SetForegroundWindow from any process
        - Works in Scintilla/Notepad++, Telegram, VS Code, browsers, etc.
        """
        import ctypes
        import ctypes.wintypes
        import time

        VK_CONTROL = 0x11
        VK_V = 0x56
        KEYEVENTF_KEYUP = 0x0002

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # Get target window handle
        hwnd = self._target_hwnd if self._target_hwnd else user32.GetForegroundWindow()
        if not hwnd:
            hwnd = user32.GetForegroundWindow()
        if not hwnd:
            logger.warning("_simulate_paste_windows: no window handle")
            return False

        # Log target info
        try:
            length = user32.GetWindowTextLengthW(hwnd) + 1
            buf = ctypes.create_unicode_buffer(length)
            user32.GetWindowTextW(hwnd, buf, length)
            logger.info(f"_simulate_paste_windows: target hwnd={hwnd}, title='{buf.value[:60]}'")
        except Exception:
            logger.info(f"_simulate_paste_windows: target hwnd={hwnd}")

        logger.info("_simulate_paste_windows: sleeping 0.3s for clipboard to settle")
        time.sleep(0.3)

        # ----------------------------------------------------------------
        # Method 1: AttachThreadInput + SetForegroundWindow + keybd_event
        # ----------------------------------------------------------------
        old_foreground = 0
        try:
            # Save current foreground window for later restoration
            old_foreground = user32.GetForegroundWindow()

            # Get thread IDs
            current_tid = kernel32.GetCurrentThreadId()
            target_tid = user32.GetWindowThreadProcessId(hwnd, None)
            old_foreground_tid = 0
            if old_foreground and old_foreground != hwnd:
                old_foreground_tid = user32.GetWindowThreadProcessId(old_foreground, None)

            # Attach our thread to target window's input thread
            # This allows SetForegroundWindow to work cross-process
            if current_tid != target_tid:
                user32.AttachThreadInput(current_tid, target_tid, True)

            # Also attach to old foreground's thread to properly restore later
            if old_foreground_tid and old_foreground_tid not in (current_tid, target_tid):
                user32.AttachThreadInput(current_tid, old_foreground_tid, True)

            # Bring target window to foreground
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            # Only restore if minimized — avoid normalizing the window
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE

            time.sleep(0.1)

            # Send Ctrl+V via keybd_event (NOT blocked by UIPI)
            logger.info("_simulate_paste_windows: method 1 - keybd_event Ctrl+V")
            user32.keybd_event(VK_CONTROL, 0, 0, 0)        # Ctrl down
            time.sleep(0.05)
            user32.keybd_event(VK_V, 0, 0, 0)               # V down
            time.sleep(0.05)
            user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0) # V up
            time.sleep(0.05)
            user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)  # Ctrl up
            time.sleep(0.1)

            logger.info("_simulate_paste_windows: keybd_event Ctrl+V sent successfully")
            return True

        except Exception as e:
            logger.error(f"_simulate_paste_windows: method 1 failed: {e}")

        finally:
            # Detach threads
            try:
                current_tid = kernel32.GetCurrentThreadId()
                if old_foreground_tid and old_foreground_tid != current_tid:
                    user32.AttachThreadInput(current_tid, old_foreground_tid, False)
                if target_tid and target_tid != current_tid:
                    user32.AttachThreadInput(current_tid, target_tid, False)
            except Exception:
                pass

        # ----------------------------------------------------------------
        # Method 2: PostMessage WM_PASTE to saved edit child (if available)
        # ----------------------------------------------------------------
        edit_hwnd = self._target_edit_hwnd
        if edit_hwnd:
            try:
                logger.info("_simulate_paste_windows: method 2 - PostMessage WM_PASTE to edit child hwnd=%d", edit_hwnd)
                # Check if Scintilla
                buffer = ctypes.create_unicode_buffer(128)
                user32.GetClassNameW(edit_hwnd, buffer, 128)
                class_name = buffer.value

                if class_name == 'Scintilla':
                    # For Scintilla: SCI_PASTE (2182) must be sent via SendMessage
                    logger.info("_simulate_paste_windows: method 2 - Scintilla SendMessage SCI_PASTE")
                    user32.SendMessageTimeoutW(edit_hwnd, 2182, 0, 0, 0x0002, 500, None)
                    time.sleep(0.2)
                    return True
                else:
                    # For standard Edit/RichEdit: PostMessage WM_PASTE
                    result = user32.PostMessageW(edit_hwnd, 0x0302, 0, 0)
                    if result != 0:
                        time.sleep(0.2)
                        return True
            except Exception as e:
                logger.error("_simulate_paste_windows: method 2 failed: %s", e)

        # ----------------------------------------------------------------
        # Method 3: SendInput (may be blocked by UIPI but works for same-integrity)
        # ----------------------------------------------------------------
        try:
            INPUT_KEYBOARD = 1

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [
                    ("wVk", ctypes.wintypes.WORD),
                    ("wScan", ctypes.wintypes.WORD),
                    ("dwFlags", ctypes.wintypes.DWORD),
                    ("time", ctypes.wintypes.DWORD),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
                ]

            class INPUT(ctypes.Structure):
                _fields_ = [
                    ("type", ctypes.wintypes.DWORD),
                    ("ki", KEYBDINPUT),
                ]

            SendInput = user32.SendInput
            SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
            SendInput.restype = ctypes.c_uint

            def send_key(vk, flags=0):
                inp = INPUT()
                inp.type = INPUT_KEYBOARD
                inp.ki = KEYBDINPUT(vk, 0, flags, 0, None)
                SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

            logger.info("_simulate_paste_windows: method 3 - SendInput Ctrl+V")
            send_key(VK_CONTROL)
            time.sleep(0.05)
            send_key(VK_V)
            time.sleep(0.05)
            send_key(VK_V, KEYEVENTF_KEYUP)
            time.sleep(0.05)
            send_key(VK_CONTROL, KEYEVENTF_KEYUP)
            time.sleep(0.05)
            logger.info("_simulate_paste_windows: SendInput SUCCESS")
            return True
        except Exception as e:
            logger.error(f"_simulate_paste_windows: SendInput failed: {e}")

        # ----------------------------------------------------------------
        # Method 4: pynput
        # ----------------------------------------------------------------
        try:
            from pynput.keyboard import Controller as KBController, Key

            logger.info("_simulate_paste_windows: method 4 - pynput Ctrl+V")
            kb = KBController()
            kb.press(Key.ctrl)
            time.sleep(0.05)
            kb.press("v")
            time.sleep(0.05)
            kb.release("v")
            time.sleep(0.05)
            kb.release(Key.ctrl)
            time.sleep(0.05)
            logger.info("_simulate_paste_windows: pynput SUCCESS")
            return True
        except Exception as e:
            logger.error(f"_simulate_paste_windows: pynput paste failed: {e}")

        # ----------------------------------------------------------------
        # Method 5: pyautogui
        # ----------------------------------------------------------------
        try:
            import pyautogui
            logger.info("_simulate_paste_windows: method 5 - pyautogui Ctrl+V")
            pyautogui.hotkey("ctrl", "v")
            logger.info("_simulate_paste_windows: pyautogui SUCCESS")
            return True
        except Exception as e:
            logger.error(f"_simulate_paste_windows: pyautogui paste failed: {e}")

        logger.error("_simulate_paste_windows: ALL METHODS FAILED")
        return False

    def _paste_to_console(self, text: str) -> bool:
        """Paste text to console window (CMD, PowerShell) with fallback methods.

        Console windows don't support Ctrl+V paste reliably.
        Tries multiple methods: PostMessage WM_CHAR, SendInput, keybd_event.
        """
        import ctypes
        import ctypes.wintypes
        import time

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnd = self._target_hwnd if self._target_hwnd else user32.GetForegroundWindow()
        if not hwnd:
            logger.warning("_paste_to_console: no window handle")
            return False

        logger.info(f"_paste_to_console: sending {len(text)} chars to hwnd={hwnd}")

        # Method 1: Try PostMessage WM_CHAR
        success_count = 0
        for char in text:
            result = user32.PostMessageW(hwnd, 0x0102, ord(char), 0)  # WM_CHAR
            if result != 0:
                success_count += 1
            time.sleep(0.005)

        if success_count > len(text) * 0.5:
            logger.info(f"_paste_to_console: PostMessageW succeeded for {success_count}/{len(text)} chars")
            return True

        # Method 2: Try SendInput with keybd_event for each character
        logger.info("_paste_to_console: PostMessageW failed, trying SendInput method")
        try:
            VK_CONTROL = 0x11
            VK_V = 0x56
            KEYEVENTF_KEYUP = 0x0002

            # Save clipboard
            old_clipboard = ""
            try:
                import pyperclip
                old_clipboard = pyperclip.paste()
            except Exception:
                pass

            # Copy text to clipboard
            try:
                import pyperclip
                pyperclip.copy(text)
            except Exception:
                try:
                    import subprocess as sp
                    proc = sp.Popen(["clip.exe"], stdin=sp.PIPE, shell=True)
                    encoded = text.encode("utf-16-le") + b"\0\0"
                    proc.communicate(input=encoded)
                except Exception:
                    logger.error("_paste_to_console: clipboard copy failed")
                    return False

            time.sleep(0.1)

            # Send Ctrl+V via keybd_event
            old_fg = user32.GetForegroundWindow()
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.05)

            user32.keybd_event(VK_CONTROL, 0, 0, 0)
            time.sleep(0.02)
            user32.keybd_event(VK_V, 0, 0, 0)
            time.sleep(0.02)
            user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.02)
            user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
            time.sleep(0.1)

            # Restore focus
            if old_fg and old_fg != hwnd:
                user32.SetForegroundWindow(old_fg)

            # Restore clipboard
            try:
                import pyperclip
                pyperclip.copy(old_clipboard)
            except Exception:
                pass

            logger.info("_paste_to_console: SendInput method completed")
            return True

        except Exception as e:
            logger.error(f"_paste_to_console: SendInput failed: {e}")

        # Method 3: Try character typing as last resort
        logger.info("_paste_to_console: trying character typing fallback")
        try:
            for char in text:
                user32.PostMessageW(hwnd, 0x0102, ord(char), 0)  # WM_CHAR
                time.sleep(0.01)
            logger.info("_paste_to_console: character typing completed")
            return True
        except Exception as e:
            logger.error(f"_paste_to_console: character typing failed: {e}")

        logger.error("_paste_to_console: ALL METHODS FAILED")
        return False

    def _simulate_paste_macos(self) -> bool:
        """Simulate Cmd+V on macOS."""
        try:
            import pyautogui
            pyautogui.hotkey("command", "v")
            return True
        except Exception as e:
            print(f"macOS paste failed: {e}")
            return False

    def _simulate_paste_linux(self) -> bool:
        """Simulate Ctrl+V on Linux using evdev or pyautogui."""
        # Try evdev first (works on Wayland)
        if self._evdev_available and self._uinput:
            try:
                return self._paste_evdev()
            except Exception as e:
                print(f"evdev paste failed: {e}")

        # Fallback to PyAutoGUI (works on X11)
        try:
            import pyautogui
            pyautogui.hotkey("ctrl", "v")
            return True
        except Exception as e:
            print(f"PyAutoGUI paste failed: {e}")

        return False

    def _paste_evdev(self) -> bool:
        """Simulate Ctrl+V using evdev UInput."""
        ecodes = self._ecodes

        # Press Ctrl
        self._uinput.write(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 1)
        self._uinput.syn()
        time.sleep(0.01)

        # Press V
        self._uinput.write(ecodes.EV_KEY, ecodes.KEY_V, 1)
        self._uinput.syn()
        time.sleep(0.01)

        # Release V
        self._uinput.write(ecodes.EV_KEY, ecodes.KEY_V, 0)
        self._uinput.syn()
        time.sleep(0.01)

        # Release Ctrl
        self._uinput.write(ecodes.EV_KEY, ecodes.KEY_LEFTCTRL, 0)
        self._uinput.syn()

        return True

    def _type_characters(self, text: str) -> bool:
        """
        Type text character by character into the currently focused window.

        Args:
            text: Text to type

        Returns:
            True if successful, False otherwise
        """
        if self.system == "Windows" or self.system == "Darwin":
            return self._type_pyautogui(text)
        else:
            return self._type_linux(text)

    def _type_pyautogui(self, text: str) -> bool:
        """Type text using PyAutoGUI (Windows/macOS)."""
        try:
            import pyautogui

            # Small delay to let focus settle
            time.sleep(0.1)
            pyautogui.write(text, interval=self._typing_delay)
            return True
        except Exception as e:
            print(f"PyAutoGUI typing error: {e}")
            return self.copy_to_clipboard(text)

    def _type_linux(self, text: str) -> bool:
        """Type text on Linux using evdev UInput."""
        # Small delay to let focus settle after our window hides
        time.sleep(0.05)

        if self._evdev_available and self._uinput:
            try:
                return self._type_evdev(text)
            except Exception as e:
                print(f"evdev typing failed: {e}")

        # Fallback to PyAutoGUI (works on X11)
        try:
            import pyautogui

            pyautogui.write(text, interval=self._typing_delay)
            return True
        except Exception as e:
            print(f"PyAutoGUI fallback failed: {e}")

        # Last resort: clipboard
        if self.copy_to_clipboard(text):
            print("Text copied to clipboard - press Ctrl+V to paste")
            return True

        return False

    def _type_evdev(self, text: str) -> bool:
        """Type text using evdev UInput (works on Wayland)."""
        ecodes = self._ecodes

        for char in text:
            if char in self._key_map:
                keycode, needs_shift = self._key_map[char]

                if needs_shift:
                    self._uinput.write(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 1)
                    self._uinput.syn()

                # Key press
                self._uinput.write(ecodes.EV_KEY, keycode, 1)
                self._uinput.syn()
                # Key release
                self._uinput.write(ecodes.EV_KEY, keycode, 0)
                self._uinput.syn()

                if needs_shift:
                    self._uinput.write(ecodes.EV_KEY, ecodes.KEY_LEFTSHIFT, 0)
                    self._uinput.syn()

                # Small delay between characters to prevent dropped keystrokes
                time.sleep(self._typing_delay)

        return True

    def get_clipboard_text(self) -> str:
        """
        Get text from clipboard.

        Returns:
            Current clipboard text, or empty string if clipboard is empty or on error
        """
        if self.system == "Windows":
            try:
                import pyperclip
                text = pyperclip.paste()
                logger.info(f"get_clipboard_text: pyperclip.paste() returned {len(text)} chars")
                return text
            except Exception as e:
                logger.error(f"get_clipboard_text (pyperclip): {e}")
                return ""

        if self.system == "Darwin":
            try:
                proc = subprocess.Popen(
                    ["pbpaste"],
                    stdout=subprocess.PIPE,
                )
                output, _ = proc.communicate()
                if proc.returncode == 0:
                    return output.decode("utf-8")
            except Exception:
                pass
            return ""

        # Linux - try multiple clipboard tools
        clipboard_commands = [
            ["wl-paste"],  # Wayland
            ["xclip", "-selection", "clipboard", "-o"],  # X11
            ["xsel", "--clipboard", "--output"],  # X11 alternative
        ]

        for cmd in clipboard_commands:
            if shutil.which(cmd[0]):
                try:
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    output, _ = proc.communicate()
                    if proc.returncode == 0:
                        return output.decode("utf-8")
                except Exception:
                    pass

        return ""

    def copy_to_clipboard(self, text: str) -> bool:
        """
        Copy text to clipboard.

        Args:
            text: Text to copy

        Returns:
            True if successful, False otherwise
        """
        if self.system == "Windows":
            # Try pyperclip first
            try:
                import pyperclip
                pyperclip.copy(text)
                logger.info("copy_to_clipboard: pyperclip.copy() succeeded")
                return True
            except Exception as e:
                logger.error(f"copy_to_clipboard (pyperclip): {e}")

            # Fallback to clip.exe (Windows built-in)
            try:
                import subprocess as sp
                proc = sp.Popen(
                    ["clip.exe"],
                    stdin=sp.PIPE,
                    shell=True,
                )
                # clip.exe expects UTF-16LE with BOM
                import io as _io
                encoded = text.encode("utf-16-le") + b"\0\0"
                proc.communicate(input=encoded)
                if proc.returncode == 0:
                    logger.info("copy_to_clipboard: clip.exe succeeded")
                    return True
                logger.warning(f"copy_to_clipboard: clip.exe returned {proc.returncode}")
            except Exception as e2:
                logger.error(f"copy_to_clipboard (clip.exe): {e2}")

            logger.error("copy_to_clipboard: all Windows methods failed")
            return False

        if self.system == "Darwin":
            try:
                proc = subprocess.Popen(
                    ["pbcopy"],
                    stdin=subprocess.PIPE,
                )
                proc.communicate(input=text.encode("utf-8"))
                return proc.returncode == 0
            except Exception:
                pass
            return False

        # Linux - try multiple clipboard tools
        clipboard_commands = [
            ["wl-copy"],  # Wayland
            ["xclip", "-selection", "clipboard"],  # X11
            ["xsel", "--clipboard", "--input"],  # X11 alternative
        ]

        for cmd in clipboard_commands:
            if shutil.which(cmd[0]):
                try:
                    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                    proc.communicate(input=text.encode("utf-8"))
                    if proc.returncode == 0:
                        return True
                except Exception:
                    pass

        return False

    def __del__(self):
        """Clean up UInput device."""
        if self._uinput:
            try:
                self._uinput.close()
            except Exception:
                pass
