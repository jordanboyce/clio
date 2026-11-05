"""Recordings reach Whisper: which files are accepted, and that they really decode.

The decode tests need requirements-audio.txt, which CI installs. They exist
because a PyAV release once broke every transcription while nothing noticed:
faster-whisper and PyAV both imported fine, and the failure only appeared when
a real file was opened.
"""

import io
import sys
import wave
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import audio_transcriber
from services.code_extractor import is_code_file
from services.document_extractor import DocumentExtractor, is_supported_filename

VIDEO_NAMES = ["call.mp4", "call.m4v", "call.mov", "call.mkv", "call.webm", "call.avi", "call.wmv", "call.mpeg"]
AUDIO_NAMES = ["call.mp3", "call.wav", "call.m4a", "call.aac", "call.wma", "call.opus", "call.ogg", "call.flac"]


@pytest.mark.parametrize("name", VIDEO_NAMES + AUDIO_NAMES + ["CALL.MKV"])
def test_recordings_are_accepted(name):
    assert is_supported_filename(name)
    assert audio_transcriber.is_audio_file(Path(name))


def test_typescript_is_not_mistaken_for_a_video():
    assert ".ts" not in audio_transcriber.AUDIO_EXTENSIONS
    assert is_code_file("app.ts")


def test_no_recording_extension_is_claimed_by_the_code_extractor():
    # The extractor tries code first, so a shared suffix would never be transcribed.
    assert not [ext for ext in audio_transcriber.AUDIO_EXTENSIONS if is_code_file(f"file{ext}")]


def test_video_upload_is_transcribed_with_timestamps(tmp_path, monkeypatch):
    path = tmp_path / "standup.mkv"
    path.write_bytes(b"not decoded: the transcriber is a stand-in")
    segments = [
        {"start": 5.0, "end": 8.0, "text": "Launch moves to March."},
        {"start": 250.0, "end": 253.0, "text": "Two more engineers."},
    ]
    stand_in = SimpleNamespace(transcribe=lambda audio_path, language=None: audio_transcriber.TranscriptionResult(
        text="", segments=segments, language="en", duration=253.0))
    monkeypatch.setattr(audio_transcriber, "get_transcriber", lambda **_: stand_in)

    result = DocumentExtractor(enable_ocr=False).extract_text(path)

    assert result.page_texts == {1: "[00:05] Launch moves to March.", 2: "[04:10] Two more engineers."}


def _stand_in_whisper(monkeypatch, baked):
    """A faster_whisper whose local-only lookup finds a model only when `baked`."""
    from huggingface_hub.utils import LocalEntryNotFoundError

    calls = []

    def whisper_model(size, **options):
        calls.append(options)
        if options.get("local_files_only") and not baked:
            raise LocalEntryNotFoundError("not in the local cache")
        return SimpleNamespace(size=size)

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=whisper_model))
    return calls


def test_a_baked_model_is_used_without_downloading(monkeypatch):
    calls = _stand_in_whisper(monkeypatch, baked=True)

    audio_transcriber.AudioTranscriber()._ensure_loaded()

    assert [call.get("local_files_only") for call in calls] == [True]
    assert calls[0]["download_root"] is None


def test_a_missing_model_downloads_into_the_data_directory(monkeypatch, tmp_path):
    # In the image HF_HOME is read-only to the service user, so a download
    # there fails with a permission error on every upload.
    monkeypatch.setattr("config.settings.data_dir", tmp_path)
    calls = _stand_in_whisper(monkeypatch, baked=False)

    audio_transcriber.AudioTranscriber()._ensure_loaded()

    assert calls[-1]["download_root"] == str(tmp_path / "models" / "whisper")
    assert not calls[-1].get("local_files_only")


def test_an_earlier_download_is_used_without_touching_the_network(monkeypatch, tmp_path):
    from huggingface_hub.utils import LocalEntryNotFoundError

    monkeypatch.setattr("config.settings.data_dir", tmp_path)
    downloads = str(tmp_path / "models" / "whisper")
    calls = []

    def whisper_model(size, **options):
        calls.append(options)
        if options["download_root"] != downloads:
            raise LocalEntryNotFoundError("not in HF_HOME")
        return SimpleNamespace(size=size)

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=whisper_model))

    audio_transcriber.AudioTranscriber()._ensure_loaded()

    assert all(call["local_files_only"] for call in calls)


def test_a_blocked_download_explains_itself_and_is_not_retried_per_file(monkeypatch, tmp_path):
    from huggingface_hub.utils import LocalEntryNotFoundError

    monkeypatch.setattr("config.settings.data_dir", tmp_path)
    attempts = []

    def whisper_model(size, **options):
        if not options.get("local_files_only"):
            attempts.append(size)
        raise LocalEntryNotFoundError("hub unreachable; set HF_HUB_OFFLINE=0")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=whisper_model))
    transcriber = audio_transcriber.AudioTranscriber()

    for _ in range(3):
        with pytest.raises(audio_transcriber.TranscriptionModelUnavailable) as caught:
            transcriber._ensure_loaded()

    assert len(attempts) == 1
    assert "docs/AIRGAP.md" in str(caught.value)
    assert "Other kinds of source are not affected" in str(caught.value)
    assert "HF_HUB_OFFLINE" not in str(caught.value)

    # Once the window has passed it tries again, so a later fix is picked up.
    transcriber._retry_at = 0.0
    with pytest.raises(audio_transcriber.TranscriptionModelUnavailable):
        transcriber._ensure_loaded()
    assert len(attempts) == 2


def test_readiness_does_not_pass_audio_when_the_model_cannot_be_obtained(monkeypatch, tmp_path):
    pytest.importorskip("faster_whisper")
    from services import embedder, readiness

    monkeypatch.setattr("config.settings.data_dir", tmp_path)
    monkeypatch.setattr(audio_transcriber, "model_on_disk", lambda size: False)
    monkeypatch.setattr(embedder, "hub_offline", lambda: True)

    check = readiness._audio_check()

    assert check["status"] == readiness.WARN
    assert "Everything else works" in str(check)


def test_decoder_reads_a_real_file():
    pytest.importorskip("faster_whisper")
    from faster_whisper.audio import decode_audio

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(bytes(32000))
    buffer.seek(0)

    assert len(decode_audio(buffer)) == 16000


@pytest.mark.parametrize("ext, audio_codec", [(".mp4", "aac"), (".mov", "aac"), (".mkv", "aac"), (".avi", "mp3")])
def test_decoder_takes_the_audio_track_from_a_video(tmp_path, ext, audio_codec):
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    pytest.importorskip("faster_whisper")
    from faster_whisper.audio import decode_audio

    rate, seconds = 48000, 1
    path = tmp_path / f"call{ext}"
    with av.open(str(path), "w") as out:
        audio = out.add_stream(audio_codec, rate=rate)
        video = out.add_stream("mpeg4", rate=10)
        video.width, video.height, video.pix_fmt = 64, 64, "yuv420p"

        tone = (0.2 * np.sin(2 * np.pi * 440 * np.arange(rate * seconds) / rate)).astype(np.float32)
        source = av.AudioFrame.from_ndarray(tone.reshape(1, -1), format="flt", layout="mono")
        source.sample_rate, source.pts, source.time_base = rate, 0, Fraction(1, rate)
        resampler = av.AudioResampler(format=audio.codec_context.format.name, layout="mono", rate=rate)
        for frame in resampler.resample(source) + resampler.resample(None):
            out.mux(audio.encode(frame))
        out.mux(audio.encode(None))

        for index in range(seconds * 10):
            frame = av.VideoFrame.from_ndarray(np.zeros((64, 64, 3), dtype=np.uint8), format="rgb24")
            frame.pts = index
            out.mux(video.encode(frame))
        out.mux(video.encode(None))

    assert len(decode_audio(str(path))) == pytest.approx(16000 * seconds, rel=0.1)
