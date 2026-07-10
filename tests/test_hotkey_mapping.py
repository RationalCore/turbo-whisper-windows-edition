"""Test hotkey key mapping for Russian keyboard layout."""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PyQt6.QtCore import Qt


# Russian letters to Latin QWERTY mapping (JCUKEN layout)
RUSSIAN_TO_LATIN = {
    "\u0430": "F",   # а -> F
    "\u0431": ",",   # б -> ,
    "\u0432": "D",   # в -> D
    "\u0433": "U",   # г -> U
    "\u0434": "L",   # д -> L
    "\u0435": "T",   # е -> T
    "\u0436": ";",   # ж -> ;
    "\u0437": "P",   # з -> P
    "\u0438": "B",   # и -> B
    "\u0439": "Q",   # й -> Q
    "\u043a": "R",   # к -> R
    "\u043b": "K",   # л -> K
    "\u043c": "V",   # м -> V
    "\u043d": "Y",   # н -> Y
    "\u043e": "J",   # о -> J
    "\u043f": "G",   # п -> G
    "\u0440": "H",   # р -> H
    "\u0441": "C",   # с -> C
    "\u0442": "N",   # т -> N
    "\u0443": "E",   # у -> E
    "\u0444": "A",   # ф -> A
    "\u0445": "[",   # х -> [
    "\u0446": "W",   # ц -> W
    "\u0447": "X",   # ч -> X
    "\u0448": "I",   # ш -> I
    "\u0449": "O",   # щ -> O
    "\u044a": "]",   # ъ -> ]
    "\u044b": "S",   # ы -> S
    "\u044c": "M",   # ь -> M
    "\u044d": "'",   # э -> '
    "\u044e": ".",   # ю -> .
    "\u044f": "Z",   # я -> Z
}


def pynput_key_to_name(key) -> str:
    """Convert pynput key to our key name format."""
    from pynput import keyboard

    # Special keys
    special_keys = {
        keyboard.Key.space: "Space",
        keyboard.Key.tab: "Tab",
        keyboard.Key.enter: "Enter",
        keyboard.Key.esc: "Escape",
        keyboard.Key.backspace: "Backspace",
        keyboard.Key.delete: "Delete",
        keyboard.Key.insert: "Insert",
        keyboard.Key.home: "Home",
        keyboard.Key.end: "End",
        keyboard.Key.page_up: "PageUp",
        keyboard.Key.page_down: "PageDown",
        keyboard.Key.up: "ArrowUp",
        keyboard.Key.down: "ArrowDown",
        keyboard.Key.left: "ArrowLeft",
        keyboard.Key.right: "ArrowRight",
        keyboard.Key.caps_lock: "CapsLock",
        keyboard.Key.num_lock: "NumLock",
        keyboard.Key.scroll_lock: "ScrollLock",
        keyboard.Key.print_screen: "PrintScreen",
        keyboard.Key.pause: "Pause",
        keyboard.Key.f1: "F1", keyboard.Key.f2: "F2", keyboard.Key.f3: "F3",
        keyboard.Key.f4: "F4", keyboard.Key.f5: "F5", keyboard.Key.f6: "F6",
        keyboard.Key.f7: "F7", keyboard.Key.f8: "F8", keyboard.Key.f9: "F9",
        keyboard.Key.f10: "F10", keyboard.Key.f11: "F11", keyboard.Key.f12: "F12",
    }

    if key in special_keys:
        return special_keys[key]

    # Character keys
    try:
        char = key.char
        if char:
            # Handle tilde/grave variants
            if char in ("~", "`", "\u0451"):  # ~, `, ё
                return "~"
            # Latin letters - uppercase
            if char.isalpha() and ord(char) < 128:
                return char.upper()
            # Numbers
            if char.isdigit():
                return char
            # Russian letters - map to Latin QWERTY equivalents
            if char in RUSSIAN_TO_LATIN:
                return RUSSIAN_TO_LATIN[char]
            # Other printable characters
            if char.isprintable():
                return char.upper()
    except AttributeError:
        pass

    return ""


# Test cases: (input_char, expected_output)
TEST_CASES = [
    # Latin letters (should stay as-is, uppercase)
    ("a", "A"), ("b", "B"), ("c", "C"), ("d", "D"), ("e", "E"),
    ("f", "F"), ("g", "G"), ("h", "H"), ("i", "I"), ("j", "J"),
    ("k", "K"), ("l", "L"), ("m", "M"), ("n", "N"), ("o", "O"),
    ("p", "P"), ("q", "Q"), ("r", "R"), ("s", "S"), ("t", "T"),
    ("u", "U"), ("v", "V"), ("w", "W"), ("x", "X"), ("y", "Y"), ("z", "Z"),

    # Numbers
    ("0", "0"), ("1", "1"), ("2", "2"), ("3", "3"), ("4", "4"),
    ("5", "5"), ("6", "6"), ("7", "7"), ("8", "8"), ("9", "9"),

    # Special characters
    ("~", "~"), ("`", "~"), ("\u0451", "~"),  # ё -> ~

    # Russian letters -> Latin QWERTY equivalents
    ("\u0430", "F"),   # а -> F
    ("\u0431", ","),   # б -> ,
    ("\u0432", "D"),   # в -> D
    ("\u0433", "U"),   # г -> U
    ("\u0434", "L"),   # д -> L
    ("\u0435", "T"),   # е -> T
    ("\u0436", ";"),   # ж -> ;
    ("\u0437", "P"),   # з -> P
    ("\u0438", "B"),   # и -> B
    ("\u0439", "Q"),   # й -> Q
    ("\u043a", "R"),   # к -> R
    ("\u043b", "K"),   # л -> K
    ("\u043c", "V"),   # м -> V
    ("\u043d", "Y"),   # н -> Y
    ("\u043e", "J"),   # о -> J
    ("\u043f", "G"),   # п -> G
    ("\u0440", "H"),   # р -> H
    ("\u0441", "C"),   # с -> C
    ("\u0442", "N"),   # т -> N
    ("\u0443", "E"),   # у -> E
    ("\u0444", "A"),   # ф -> A
    ("\u0445", "["),   # х -> [
    ("\u0446", "W"),   # ц -> W
    ("\u0447", "X"),   # ч -> X
    ("\u0448", "I"),   # ш -> I
    ("\u0449", "O"),   # щ -> O
    ("\u044a", "]"),   # ъ -> ]
    ("\u044b", "S"),   # ы -> S
    ("\u044c", "M"),   # ь -> M
    ("\u044d", "'"),   # э -> '
    ("\u044e", "."),   # ю -> .
    ("\u044f", "Z"),   # я -> Z
]


def test_key_mapping():
    """Test that key mapping works correctly."""
    print("Testing key mapping...")
    print("=" * 60)

    passed = 0
    failed = 0

    for input_char, expected in TEST_CASES:
        # Create a mock key object
        class MockKey:
            def __init__(self, char):
                self.char = char

        key = MockKey(input_char)
        result = pynput_key_to_name(key)

        status = "PASS" if result == expected else "FAIL"
        if result == expected:
            passed += 1
        else:
            failed += 1
            print(f"{status}: '{input_char}' (U+{ord(input_char):04X}) -> '{result}' (expected '{expected}')")

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed, {len(TEST_CASES)} total")

    if failed == 0:
        print("All tests PASSED!")
        return True
    else:
        print(f"{failed} tests FAILED!")
        return False


if __name__ == "__main__":
    success = test_key_mapping()
    sys.exit(0 if success else 1)
