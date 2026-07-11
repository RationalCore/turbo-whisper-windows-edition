"""Test hallucination filter with exact match logic."""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from turbo_whisper.main import _is_hallucination

# (text, expected_is_hallucination)
TEST_CASES = [
    ("Я не понимаю, что происходит. Расскажи мне, пожалуйста, что происходит? Почему ни хера не работает?", False),
    ("Привет! Проверка раз, два, три.", False),
    ("Продолжение следует...", True),
    ("Спасибо.", True),
    ("Thank you for watching.", True),
    ("Это тестовое сообщение", False),
    ("Субтитры создавал DimaTorzok", True),
    ("Субтитры сделал DimaTorzok", True),
    ("Девушки отдыхают...", True),
    ("Пока-пока! Удачи!", True),
    ("Редактор субтитров А.Семкин Корректор А.Егорова", True),
    ("Смотрите продолжение во второй части видео.", True),
    ("Субтитры от сообщества Amara.org", True),
]

def test_hallucination_filter():
    print("Testing hallucination filter...")
    print("=" * 60)

    passed = 0
    failed = 0
    for text, expected in TEST_CASES:
        result = _is_hallucination(text)
        status = "PASS" if result == expected else "FAIL"
        if result == expected:
            passed += 1
        else:
            failed += 1
            exp_str = "HALLUCINATION" if expected else "VALID"
            got_str = "HALLUCINATION" if result else "VALID"
            print(f"{status}: '{text[:50]}' -> {got_str} (expected {exp_str})")

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed, {len(TEST_CASES)} total")

    if failed == 0:
        print("All tests PASSED!")
        return True
    else:
        print(f"{failed} tests FAILED!")
        return False

if __name__ == "__main__":
    success = test_hallucination_filter()
    sys.exit(0 if success else 1)
