"""
Server-side speech-to-text via faster-whisper (CTranslate2/Whisper).

Deliberately server-side rather than the browser's Web Speech API: Safari
(desktop and iOS) has never implemented SpeechRecognition at all, so a
browser-native approach permanently excludes every iPhone user -- which is
most of this team. The frontend instead records audio with the universally-
supported MediaRecorder API and uploads the clip here for transcription,
so voice input works the same way on every modern browser/device.

Runs on CPU deliberately, not the shared GPU -- Ollama's vision/text models
already sit close to the RTX 3090's VRAM ceiling on this devserver, and a
short claim-answer clip (a few seconds to ~30s) transcribes in a few
seconds on CPU even with the larger "small.en" model, so there's no real
latency reason to contend for GPU memory.

"small.en" over "base.en": a live test caught "Thika Road" being
transcribed as "Vicar rd" -- base.en's accuracy on Kenyan place names
(unsurprising, it has never heard them) was bad enough to actually corrupt
claim narratives. small.en has a meaningfully lower word-error-rate, and
`KENYA_CONTEXT_PROMPT` below biases decoding toward the specific vocabulary
this app's claims actually contain (roads, towns, common Kenyan names),
which helps more than model size alone -- Whisper's initial_prompt biases
the decoder toward tokens it contains without forcing them onto unrelated
audio.
"""

import logging
import os
import tempfile

logger = logging.getLogger(__name__)

_model = None
_model_lock_msg_shown = False

WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "small.en")

KENYA_CONTEXT_PROMPT = (
    "Kenyan motor insurance claim. Roads and places mentioned may include "
    "Thika Road, Mombasa Road, Waiyaki Way, Ngong Road, Uhuru Highway, "
    "Jogoo Road, Outer Ring Road, Langata Road, Mbagathi Way, Enterprise Road, "
    "Nairobi, Westlands, Karen, Muthaiga, Eastleigh, Kayole, Embakasi, Kasarani, "
    "Nakuru, Mombasa, Kisumu, Eldoret, Nyeri, Machakos, matatu, boda boda, "
    "bumper, windscreen, bonnet, headlamp."
)


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        logger.info(f"Loading Whisper model '{WHISPER_MODEL_SIZE}' (CPU, int8) — first call only...")
        _model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
        logger.info("Whisper model loaded")
    return _model


def transcribe_audio(audio_bytes: bytes, filename_hint: str = "audio.webm") -> dict:
    """
    Transcribes a recorded audio clip (webm/mp4/ogg/wav — whatever the
    browser's MediaRecorder produced; ffmpeg via PyAV decodes it) and
    returns {"success": True, "text": "..."} or {"success": False, "error": "..."}.
    Never raises -- a transcription failure should degrade to "keep typing",
    not break the claim-filing flow.
    """
    suffix = os.path.splitext(filename_hint)[1] or ".webm"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        model = _get_model()
        segments, info = model.transcribe(
            tmp_path, beam_size=5, vad_filter=True, initial_prompt=KENYA_CONTEXT_PROMPT,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()

        if not text:
            return {"success": False, "error": "Didn't catch any speech in that recording — try again."}

        return {
            "success": True,
            "text": text,
            "language": info.language,
            "language_probability": round(info.language_probability, 3),
        }
    except Exception as e:
        logger.error(f"Speech-to-text transcription failed: {str(e)}")
        return {"success": False, "error": "Transcription failed — you can type your answer instead."}
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
