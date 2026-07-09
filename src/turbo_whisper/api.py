"""Whisper API client - compatible with routerai.ru JSON API and OpenAI multipart API."""

import base64
import logging
import os
from datetime import datetime
from pathlib import Path

import httpx

from turbo_whisper.config import Config


# Setup logging to file
def _setup_logger() -> logging.Logger:
    """Set up file logger for API calls."""
    logger = logging.getLogger("turbo_whisper.api")
    logger.setLevel(logging.DEBUG)

    # Only add handler once
    if not logger.handlers:
        # Determine log path
        import sys as _sys
        if _sys.platform == "win32":
            log_dir = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        else:
            log_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        log_dir = log_dir / "turbo-whisper"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "turbo-whisper.log"

        handler = logging.FileHandler(str(log_path), encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


logger = _setup_logger()


class WhisperAPIError(Exception):
    """Error communicating with Whisper API."""

    pass


class WhisperClient:
    """Client for Whisper API supporting both JSON+base64 (routerai.ru) and multipart (OpenAI)."""

    def __init__(self, config: Config):
        self.config = config

    def _get_headers(self) -> dict[str, str]:
        """Get common headers."""
        headers = {}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    async def transcribe(self, audio_data: bytes) -> str:
        """
        Send audio to Whisper API and return transcription.

        Args:
            audio_data: WAV audio data as bytes

        Returns:
            Transcribed text
        """
        headers = self._get_headers()
        audio_size = len(audio_data)
        logger.info(f"Async transcribe start: audio_size={audio_size}B, use_json={self.config.use_json_api}")

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                if self.config.use_json_api:
                    payload = self._build_json_payload(audio_data)
                    logger.debug(f"JSON payload (without base64 data): model={payload.get('model')}, "
                                 f"format={payload.get('input_audio', {}).get('format')}, "
                                 f"language={payload.get('language', 'NOT SET')}")
                    response = await client.post(
                        self.config.api_url,
                        headers={**headers, "Content-Type": "application/json"},
                        json=payload,
                    )
                else:
                    data = {
                        "model": self.config.model or "whisper-1",
                        "language": self.config.language,
                        "response_format": "json",
                        "prompt": "Use proper punctuation: commas, periods, question marks.",
                    }
                    files = {
                        "file": ("audio.wav", audio_data, "audio/wav"),
                    }
                    response = await client.post(
                        self.config.api_url,
                        headers=headers,
                        files=files,
                        data=data,
                    )

                logger.info(f"API response: status={response.status_code}")
                if response.status_code != 200:
                    logger.error(f"API error: {response.status_code} - {response.text[:500]}")
                    raise WhisperAPIError(f"API returned {response.status_code}: {response.text[:300]}")

                result = response.json()
                text = result.get("text", "").strip()
                logger.info(f"Transcription result: text_len={len(text)}, text_preview='{text[:100]}'")
                return text

        except httpx.TimeoutException:
            logger.error("Request timed out")
            raise WhisperAPIError("Request timed out")
        except httpx.RequestError as e:
            logger.error(f"Request failed: {e}")
            raise WhisperAPIError(f"Request failed: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            raise WhisperAPIError(f"Unexpected error: {e}")

    def transcribe_sync(self, audio_data: bytes) -> str:
        """Synchronous version of transcribe with retry on 5xx errors."""
        headers = self._get_headers()
        audio_size = len(audio_data)
        logger.info(f"Sync transcribe start: audio_size={audio_size}B, use_json={self.config.use_json_api}")

        max_retries = 3
        retry_delay = 2.0  # initial delay in seconds

        for attempt in range(1, max_retries + 1):
            try:
                with httpx.Client(timeout=60.0) as client:
                    if self.config.use_json_api:
                        payload = self._build_json_payload(audio_data)
                        logger.debug(f"JSON payload (without base64 data): model={payload.get('model')}, "
                                     f"format={payload.get('input_audio', {}).get('format')}, "
                                     f"language={payload.get('language', 'NOT SET')}")
                        response = client.post(
                            self.config.api_url,
                            headers={**headers, "Content-Type": "application/json"},
                            json=payload,
                        )
                    else:
                        data = {
                            "model": self.config.model or "whisper-1",
                            "language": self.config.language,
                            "response_format": "json",
                            "prompt": "Use proper punctuation: commas, periods, question marks.",
                        }
                        files = {
                            "file": ("audio.wav", audio_data, "audio/wav"),
                        }
                        response = client.post(
                            self.config.api_url,
                            headers=headers,
                            files=files,
                            data=data,
                        )

                    logger.info(f"API response: status={response.status_code}")

                    if response.status_code == 401:
                        logger.error("Unauthorized - check API key")
                        raise WhisperAPIError("Unauthorized - check your API key in settings")
                    elif response.status_code == 403:
                        logger.error("Access denied")
                        raise WhisperAPIError("Access denied - check your API key permissions")
                    elif response.status_code == 404:
                        logger.error("API endpoint not found")
                        raise WhisperAPIError("API endpoint not found - check your API URL")
                    elif response.status_code >= 500:
                        err_msg = response.text
                        logger.error(f"Server error (attempt {attempt}/{max_retries}): "
                                   f"status={response.status_code}, "
                                   f"url={self.config.api_url}, "
                                   f"model={self.config.model}, "
                                   f"response={err_msg}")
                        if attempt < max_retries:
                            logger.info(f"Retrying in {retry_delay}s...")
                            import time
                            time.sleep(retry_delay)
                            retry_delay *= 2  # exponential backoff
                            continue
                        raise WhisperAPIError(f"Server error after {max_retries} attempts: {err_msg}")
                    elif response.status_code != 200:
                        err_msg = response.text
                        logger.error(f"API error: status={response.status_code}, "
                                   f"url={self.config.api_url}, "
                                   f"model={self.config.model}, "
                                   f"response={err_msg}")
                        raise WhisperAPIError(f"API error ({response.status_code}): {err_msg}")

                    result = response.json()
                    text = result.get("text", "").strip()
                    logger.info(f"Transcription result: text_len={len(text)}, text_preview='{text[:100]}'")
                    return text

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                logger.error(f"Request failed (attempt {attempt}/{max_retries}): {e}")
                if attempt < max_retries:
                    logger.info(f"Retrying in {retry_delay}s...")
                    import time
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                if isinstance(e, httpx.TimeoutException):
                    raise WhisperAPIError("Request timed out - server may be busy")
                else:
                    raise WhisperAPIError("Could not connect - check internet/API URL")
            except httpx.RequestError as e:
                logger.error(f"Connection error: {e}")
                raise WhisperAPIError(f"Connection error: {e}")

    def _build_json_payload(self, audio_data: bytes) -> dict:
        """Build JSON payload for routerai.ru style API.

        Converts WAV bytes to base64 and builds the request body.
        Detects format from the WAV header if possible, defaults to 'wav'.
        If language is empty or None, it is omitted from the payload.
        """
        audio_b64 = base64.b64encode(audio_data).decode("utf-8")

        # Try to detect format from WAV header (RIFF....WAVE)
        audio_format = "wav"
        if audio_data[:4] == b"RIFF" and audio_data[8:12] == b"WAVE":
            audio_format = "wav"
        elif audio_data[:4] == b"\xff\xfb" or audio_data[:4] == b"\xff\xf3":
            audio_format = "mp3"
        elif audio_data[:4] == b"fLaC":
            audio_format = "flac"
        elif audio_data[:4] == b"OggS":
            audio_format = "ogg"

        payload = {
            "model": self.config.model or "openai/whisper-large-v3-turbo",
            "input_audio": {
                "data": audio_b64,
                "format": audio_format,
            },
        }

        # Only include language if it's set (not empty)
        if self.config.language:
            payload["language"] = self.config.language
        else:
            logger.info("Language not set in config - omitting from API request (auto-detect)")

        return payload
