"""Local audio transcription via faster-whisper.

Used to turn meeting recordings (or any supported audio file) into text
so they can flow through the normal document indexing pipeline.

The Whisper model is heavy to load (hundreds of MB) and slow to initialize,
so it is lazy-loaded on first use and kept in-process for subsequent calls.
"""

from pathlib import Path
from typing import Dict, List, Optional
import logging
import threading
import time

logger = logging.getLogger(__name__)

# Video containers are listed too: only their audio track is decoded and
# transcribed, so what appears on screen is not read. .ts is left out on purpose,
# because in a source collection it means TypeScript, not an MPEG transport stream.
AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".aac", ".wma", ".opus", ".ogg", ".flac", ".mpga",
    ".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".mpeg",
}


def whisper_download_dir() -> Path:
    """Where Whisper downloads land: under the data volume, the one place the
    service user can write in the image, so a model fetched once survives
    container recreation with the data directory."""
    from config import settings
    return Path(settings.data_dir) / "models" / "whisper"


def model_on_disk(model_size: str) -> bool:
    """Whether the model can load right now with no network at all."""
    if Path(model_size).is_dir():
        return True
    from faster_whisper.utils import download_model

    for cache_dir in (None, str(whisper_download_dir())):
        try:
            download_model(model_size, local_files_only=True, cache_dir=cache_dir)
            return True
        except Exception:  # noqa: BLE001 - not here, or not a model name at all
            continue
    return False


class TranscriptionModelUnavailable(RuntimeError):
    """The speech model is not on this machine and could not be fetched."""


class TranscriptionResult:
    """Transcript plus segment-level timing from Whisper."""

    def __init__(self, text: str, segments: List[Dict], language: str, duration: float):
        self.text = text
        self.segments = segments
        self.language = language
        self.duration = duration


class AudioTranscriber:
    """Lazy-loaded faster-whisper wrapper.

    One instance is expected per process. The model is loaded on first
    transcribe() call; subsequent calls reuse it.
    """

    # A blocked download can take 20 s to give up. A folder of recordings should
    # not pay that for every file.
    RETRY_AFTER_SECONDS = 60.0

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None
        self._load_lock = threading.Lock()
        self._load_error: Optional[TranscriptionModelUnavailable] = None
        self._retry_at = 0.0

    def _ensure_loaded(self):
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            if self._load_error is not None and time.monotonic() < self._retry_at:
                raise self._load_error.with_traceback(None)
            try:
                from faster_whisper import WhisperModel
            except ImportError as e:
                raise RuntimeError(
                    "Audio transcription requires the optional audio extras. "
                    "Run `pip install -r requirements-audio.txt` (or `pip install faster-whisper`)."
                ) from e

            logger.info(
                f"Loading Whisper model '{self.model_size}' "
                f"(device={self.device}, compute_type={self.compute_type})"
            )
            self._model = self._load_model(WhisperModel)
            self._load_error = None
            logger.info("Whisper model loaded")

    def _load_model(self, whisper_model):
        """The model from disk if it is here, or else from the hub."""
        from huggingface_hub.utils import LocalEntryNotFoundError

        options = {"device": self.device, "compute_type": self.compute_type}
        downloads = whisper_download_dir()
        # A copy baked into the image or pre-seeded in HF_HOME, then an earlier
        # download. Neither touches the network. HF_HOME is read-only to the
        # service user in the image, so a download may never aim there.
        for download_root in (None, str(downloads)):
            try:
                return whisper_model(self.model_size, local_files_only=True,
                                     download_root=download_root, **options)
            except LocalEntryNotFoundError:
                continue
        try:
            return whisper_model(self.model_size, download_root=str(downloads), **options)
        except Exception as e:
            logger.warning(f"Could not download Whisper model '{self.model_size}': {e}")
            self._load_error = TranscriptionModelUnavailable(
                f"Recordings cannot be transcribed: the Whisper speech model '{self.model_size}' is not on "
                f"this server and could not be downloaded. Other kinds of source are not affected. Use the "
                f"full Clio image, which includes the 'base' model, or copy the model into {downloads} from "
                f"a connected machine (see docs/AIRGAP.md)."
            )
            self._retry_at = time.monotonic() + self.RETRY_AFTER_SECONDS
            raise self._load_error from e

    def transcribe(
        self,
        audio_path: Path,
        language: Optional[str] = None,
        vad_filter: bool = True,
    ) -> TranscriptionResult:
        """Transcribe an audio file to text with segment timestamps.

        Args:
            audio_path: Path to an audio or video file (see AUDIO_EXTENSIONS).
            language: Optional language hint (e.g. "en"). None = auto-detect.
            vad_filter: Drop silent gaps via voice-activity detection.
        """
        self._ensure_loaded()

        logger.info(f"Transcribing audio: {audio_path.name}")
        segments_gen, info = self._model.transcribe(
            str(audio_path),
            language=language,
            vad_filter=vad_filter,
            beam_size=5,
        )

        segments: List[Dict] = []
        text_parts: List[str] = []
        for seg in segments_gen:
            segments.append(
                {
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text.strip(),
                }
            )
            text_parts.append(seg.text.strip())

        full_text = "\n".join(t for t in text_parts if t)

        logger.info(
            f"Transcribed {audio_path.name}: {len(segments)} segments, "
            f"{len(full_text)} chars, language={info.language}, duration={info.duration:.1f}s"
        )

        return TranscriptionResult(
            text=full_text,
            segments=segments,
            language=info.language,
            duration=info.duration,
        )


_transcriber_instance: Optional[AudioTranscriber] = None
_instance_lock = threading.Lock()


def get_transcriber(
    model_size: str = "base",
    device: str = "cpu",
    compute_type: str = "int8",
) -> AudioTranscriber:
    """Return a process-wide AudioTranscriber, creating it on first call."""
    global _transcriber_instance
    if _transcriber_instance is not None:
        return _transcriber_instance
    with _instance_lock:
        if _transcriber_instance is None:
            _transcriber_instance = AudioTranscriber(
                model_size=model_size,
                device=device,
                compute_type=compute_type,
            )
        return _transcriber_instance


def is_audio_file(file_path: Path) -> bool:
    return file_path.suffix.lower() in AUDIO_EXTENSIONS


def format_transcript_with_timestamps(result: TranscriptionResult) -> str:
    """Render a transcript with [mm:ss] prefixes per segment.

    Useful when the transcript is indexed as a document — the timestamps
    give retrieved passages a natural anchor back into the original audio.
    """
    lines: List[str] = []
    for seg in result.segments:
        mm = int(seg["start"] // 60)
        ss = int(seg["start"] % 60)
        lines.append(f"[{mm:02d}:{ss:02d}] {seg['text']}")
    return "\n".join(lines)
