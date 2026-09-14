import { useCallback, useRef, useState } from "react";
import { transcribeAudio } from "@/lib/api";

/**
 * Lets a claimant talk instead of type. Deliberately server-side
 * transcription (Whisper, via /api/analysis/speech-to-text) rather than the
 * browser's Web Speech API: Safari (desktop AND iOS) has never implemented
 * SpeechRecognition at all, which would permanently exclude every iPhone
 * user -- most of this team included. MediaRecorder + getUserMedia, by
 * contrast, work the same way on every modern browser (Safari 14.1+,
 * Chrome, Firefox, Edge), so this works identically everywhere instead of
 * silently degrading on some devices.
 */

const CANDIDATE_MIME_TYPES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/mp4;codecs=mp4a.40.2",
  "audio/ogg;codecs=opus",
];

function pickSupportedMimeType(): string | undefined {
  if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) return undefined;
  return CANDIDATE_MIME_TYPES.find((type) => MediaRecorder.isTypeSupported(type));
}

function isSupported(): boolean {
  return (
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== "undefined"
  );
}

export function useSpeechToText({ onTranscript }: { onTranscript: (finalText: string) => void }) {
  const [listening, setListening] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const supported = isSupported();

  const cleanupStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
  }, []);

  const start = useCallback(async () => {
    if (!isSupported()) {
      setError("Voice input isn't supported in this browser.");
      return;
    }
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;

      const mimeType = pickSupportedMimeType();
      const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
      chunksRef.current = [];

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };

      recorder.onstop = async () => {
        cleanupStream();
        setListening(false);
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || "audio/webm" });
        chunksRef.current = [];
        if (blob.size < 1000) {
          // Too short to contain real speech (e.g. an accidental tap).
          return;
        }
        setTranscribing(true);
        try {
          const ext = (recorder.mimeType || "audio/webm").includes("mp4") ? "mp4" : "webm";
          const result = await transcribeAudio(blob, `answer.${ext}`);
          if (result.success && result.text) {
            onTranscript(result.text);
          } else {
            setError(result.error || "Couldn't make out that recording — you can type instead.");
          }
        } catch {
          setError("Couldn't reach the transcription service — you can type instead.");
        } finally {
          setTranscribing(false);
        }
      };

      mediaRecorderRef.current = recorder;
      recorder.start();
      setListening(true);
    } catch {
      setError("Microphone access was blocked or unavailable. Allow it in your browser settings to use voice input.");
      cleanupStream();
    }
  }, [cleanupStream, onTranscript]);

  const stop = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    } else {
      setListening(false);
      cleanupStream();
    }
  }, [cleanupStream]);

  return { supported, listening, transcribing, error, start, stop };
}
