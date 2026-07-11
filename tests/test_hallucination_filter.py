"""Test hallucination filter: unit tests + real audio transcription check."""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from turbo_whisper.main import _is_hallucination

# === Unit tests: known patterns ===
# (text, expected_is_hallucination)
UNIT_CASES = [
    # Real speech — must NOT be filtered
    ("Я не понимаю, что происходит. Расскажи мне, пожалуйста, что происходит? Почему ни хера не работает?", False),
    ("Привет! Проверка раз, два, три.", False),
    ("Это тестовое сообщение", False),
    ("Давай попробуем, как теперь работает в потоке. Непонятно, как режется.", False),
    ("Летит ёжик над Парижем. У него в носу звезда.", False),
    ("Сегодня хорошая погода, не правда ли?", False),
    ("Нужно купить хлеб, молоко и яйца.", False),

    # Single short words - filtered as likely artifact
    ("Да", True),
    ("Нет", True),
    ("Hi", True),
    ("Ok", True),
    ("Yes", True),

    # Known hallucinations — must be filtered
    ("Продолжение следует...", True),
    ("Спасибо.", True),
    ("Thank you for watching.", True),
    ("Субтитры создавал DimaTorzok", True),
    ("Субтитры сделал DimaTorzok", True),
    ("Девушки отдыхают...", True),
    ("Пока-пока! Удачи!", True),
    ("Редактор субтитров А.Семкин Корректор А.Егорова", True),
    ("Смотрите продолжение во второй части видео.", True),
    ("Субтитры от сообщества Amara.org", True),

    # Punctuation variants
    ("Продолжение следует.", True),
    ("Продолжение следует!", True),
    ("Субтитры создавал DimaTorzok.", True),
    ("Девушки отдыхают", True),
    ("Пока-пока! Удачи", True),
]


def run_unit_tests() -> bool:
    """Run unit tests against known hallucination patterns."""
    print("Unit tests:")
    print("=" * 60)
    passed = 0
    failed = 0
    for text, expected in UNIT_CASES:
        result = _is_hallucination(text)
        if result == expected:
            passed += 1
        else:
            failed += 1
            exp_str = "HALLUCINATION" if expected else "VALID"
            got_str = "HALLUCINATION" if result else "VALID"
            print(f"  FAIL: '{text[:60]}' -> {got_str} (expected {exp_str})")

    print(f"  Unit: {passed}/{len(UNIT_CASES)} passed, {failed} failed")
    return failed == 0


def test_real_recording(wav_name: str) -> bool:
    """Transcribe a real WAV file and verify the result passes the filter."""
    import wave
    import io
    from turbo_whisper.config import Config
    from turbo_whisper.api import WhisperClient

    wav_path = Path(__file__).parent.parent / wav_name
    if not wav_path.exists():
        print(f"\n  SKIP: {wav_name} not found")
        return True

    print(f"\nReal audio test: {wav_name}")
    print("=" * 60)

    # Load and validate WAV
    wav_bytes = wav_path.read_bytes()
    with wave.open(str(wav_path), "rb") as wf:
        if wf.getnchannels() != 1:
            print(f"  WARNING: expected mono, got {wf.getnchannels()} channels")

    # Transcribe
    config = Config.load()
    client = WhisperClient(config)
    text = client.transcribe_sync(wav_bytes)

    ok = text.strip() != ""
    if not ok:
        print(f"  FAIL: transcription returned empty")
        return False

    is_halluc = _is_hallucination(text)
    if is_halluc:
        print(f"  FAIL: real speech classified as hallucination!")
        print(f"  Text: '{text[:100]}'")
        return False

    print(f"  PASS: '{text[:100]}'")
    print(f"  Real speech correctly identified as valid (not hallucination)")
    return True


if __name__ == "__main__":
    unit_ok = run_unit_tests()
    live_ok = test_real_recording("test_live.wav")

    print(f"\n{'=' * 60}")
    if unit_ok and live_ok:
        print("All tests PASSED!")
        sys.exit(0)
    else:
        print(f"Some tests FAILED (unit={unit_ok}, live={live_ok})")
        sys.exit(1)
