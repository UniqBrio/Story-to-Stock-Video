"""
Whisper speech-to-text on plain onnxruntime (numpy only).

Runs the int8 Whisper exports from k2-fsa/sherpa-onnx (`small-encoder.int8.onnx`,
`small-decoder.int8.onnx`, `small-tokens.txt`) without the sherpa-onnx package.
onnxruntime's wheel is Microsoft-signed, so this loads on Windows machines where
Smart App Control blocks sherpa-onnx's unsigned DLLs.

Front end follows openai/whisper `audio.py` (MIT License, Copyright (c) 2022 OpenAI):
16 kHz audio padded to 30 s → 80-bin log-mel (n_fft 400, hop 160, Slaney mel filters).
Decoding is greedy, with the guards from openai/whisper `decoding.py` that keep music and
silence from turning into hallucinated text: a no-speech check, suppression of the model's
non-speech tokens, and a stop on repetition loops.
"""

from __future__ import annotations

import base64
from pathlib import Path

import numpy as np
import regex as re

SAMPLE_RATE = 16000
N_FFT = 400
HOP = 160
N_SAMPLES = 30 * SAMPLE_RATE          # Whisper always sees a 30 s window
N_FRAMES = N_SAMPLES // HOP           # 3000 mel frames → 1500 encoder positions
NO_SPEECH_THRESHOLD = 0.6             # openai/whisper default: P(no speech) above this → empty transcript
MAX_LOOP_PERIOD, LOOP_REPEATS = 12, 3 # an n-gram (≤ 12 tokens) repeated 3× at the end is a decoding loop
LOGPROB_THRESHOLD = -1.0              # openai/whisper default: below this average, the text is a guess


def _hz_to_mel(f):
    f = np.asanyarray(f, dtype=np.float64)
    f_sp, min_log_hz, logstep = 200.0 / 3, 1000.0, np.log(6.4) / 27.0
    mel = f / f_sp
    return np.where(f >= min_log_hz, min_log_hz / f_sp + np.log(np.maximum(f, 1e-10) / min_log_hz) / logstep, mel)


def _mel_to_hz(m):
    m = np.asanyarray(m, dtype=np.float64)
    f_sp, min_log_hz, logstep = 200.0 / 3, 1000.0, np.log(6.4) / 27.0
    min_log_mel = min_log_hz / f_sp
    return np.where(m >= min_log_mel, min_log_hz * np.exp(logstep * (m - min_log_mel)), f_sp * m)


def mel_filters(n_mels: int = 80) -> np.ndarray:
    """librosa.filters.mel(sr=16000, n_fft=400, n_mels=80) — Slaney scale and norm."""
    fft_freqs = np.linspace(0, SAMPLE_RATE / 2, 1 + N_FFT // 2)
    mel_f = _mel_to_hz(np.linspace(_hz_to_mel(0.0), _hz_to_mel(SAMPLE_RATE / 2), n_mels + 2))
    fdiff = np.diff(mel_f)
    ramps = mel_f[:, None] - fft_freqs[None, :]
    lower = -ramps[:-2] / fdiff[:-1, None]
    upper = ramps[2:] / fdiff[1:, None]
    weights = np.maximum(0, np.minimum(lower, upper))
    weights *= (2.0 / (mel_f[2:n_mels + 2] - mel_f[:n_mels]))[:, None]
    return weights.astype(np.float32)


def log_mel(audio: np.ndarray, filters: np.ndarray) -> np.ndarray:
    """(80, 3000) log-mel of up to 30 s of 16 kHz mono float audio."""
    audio = np.asarray(audio, np.float32)[:N_SAMPLES]
    audio = np.pad(audio, (0, N_SAMPLES - audio.size))
    x = np.pad(audio, (N_FFT // 2, N_FFT // 2), mode="reflect")              # torch.stft(center=True)
    frames = np.lib.stride_tricks.sliding_window_view(x, N_FFT)[::HOP]       # 3001 frames
    window = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)  # periodic Hann
    power = np.abs(np.fft.rfft(frames * window, axis=-1)) ** 2               # (3001, 201)
    mel = filters @ power[:-1].T                                             # drop the last frame → (80, 3000)
    log_spec = np.log10(np.maximum(mel, 1e-10))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    return ((log_spec + 4.0) / 4.0).astype(np.float32)


class WhisperOnnx:
    """Greedy Whisper transcription. `language=''` auto-detects per chunk."""

    def __init__(self, model_dir: Path, prefix: str = "small", language: str = "", threads: int = 4):
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        kw = {"sess_options": opts, "providers": ["CPUExecutionProvider"]}
        d = Path(model_dir)
        self.encoder = ort.InferenceSession(str(d / f"{prefix}-encoder.int8.onnx"), **kw)
        self.decoder = ort.InferenceSession(str(d / f"{prefix}-decoder.int8.onnx"), **kw)
        meta = self.encoder.get_modelmeta().custom_metadata_map
        self.n_text_layer = int(meta["n_text_layer"])
        self.n_text_ctx = int(meta["n_text_ctx"])
        self.n_text_state = int(meta["n_text_state"])
        self.sot, self.eot = int(meta["sot"]), int(meta["eot"])
        self.transcribe_tok = int(meta["transcribe"])
        self.no_timestamps = int(meta["no_timestamps"])
        self.no_speech = int(meta["no_speech"])
        self.blank = int(meta.get("blank_id", 220))
        suppress = [int(t) for t in meta.get("non_speech_tokens", "").split(",") if t.strip()]
        # never emit symbols/music glyphs or any special token (sot, language, task, timestamps …) as text
        self.suppress = np.array(sorted(set(suppress) | set(range(self.eot + 1, int(meta["n_vocab"])))), np.int64)
        codes = meta["all_language_codes"].split(",")
        toks = [int(t) for t in meta["all_language_tokens"].split(",")]
        self.lang_token = dict(zip(codes, toks))
        self.lang_tokens = np.array(toks)
        self.language = language
        self.filters = mel_filters(int(meta.get("n_mels", 80)))
        self.vocab = {}
        for line in (d / f"{prefix}-tokens.txt").read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2:
                self.vocab[int(parts[1])] = base64.b64decode(parts[0])

    def _empty_cache(self):
        shape = (self.n_text_layer, 1, self.n_text_ctx, self.n_text_state)
        return np.zeros(shape, np.float32), np.zeros(shape, np.float32)

    def _decode(self, tokens, k_cache, v_cache, cross_k, cross_v, offset, all_positions=False):
        logits, k_cache, v_cache = self.decoder.run(None, {
            "tokens": np.array([tokens], np.int64),
            "in_n_layer_self_k_cache": k_cache, "in_n_layer_self_v_cache": v_cache,
            "n_layer_cross_k": cross_k, "n_layer_cross_v": cross_v,
            "offset": np.array([offset], np.int64)})
        return (logits[0] if all_positions else logits[0, -1]), k_cache, v_cache

    @staticmethod
    def _loop_start(out: list) -> int:
        """Index where a trailing repetition loop begins (an n-gram repeated LOOP_REPEATS× at the end), else -1."""
        for period in range(1, MAX_LOOP_PERIOD + 1):
            span = period * LOOP_REPEATS
            if len(out) >= span:
                tail = out[-span:]
                if all(tail[i] == tail[i % period] for i in range(span)):
                    return len(out) - span + period          # keep one copy of the repeated n-gram
        return -1

    def detect_language(self, cross_k, cross_v) -> int:
        k, v = self._empty_cache()
        logits, _, _ = self._decode([self.sot], k, v, cross_k, cross_v, 0)
        return int(self.lang_tokens[np.argmax(logits[self.lang_tokens])])

    def transcribe(self, audio: np.ndarray) -> str:
        """Text for up to 30 s of 16 kHz mono float32 audio."""
        mel = log_mel(audio, self.filters)[None]
        cross_k, cross_v = self.encoder.run(None, {"mel": mel})
        lang = self.lang_token.get(self.language) if self.language else None
        if lang is None:
            lang = self.detect_language(cross_k, cross_v)
        prompt = [self.sot, lang, self.transcribe_tok, self.no_timestamps]
        k, v = self._empty_cache()
        all_logits, k, v = self._decode(prompt, k, v, cross_k, cross_v, 0, all_positions=True)
        first = all_logits[0].astype(np.float64)                 # prediction right after <|startoftranscript|>
        first = np.exp(first - first.max())
        self.last_no_speech_prob = float(first[self.no_speech] / first.sum())
        self.last_avg_logprob = 0.0
        if self.last_no_speech_prob > NO_SPEECH_THRESHOLD:
            return ""                                            # music / silence / noise — nothing was said
        logits = all_logits[-1]
        out, offset = [], len(prompt)
        logprob, n_scored = 0.0, 0
        while offset < self.n_text_ctx - 1:
            logits = logits.copy()
            logits[self.suppress] = -np.inf
            if not out:
                logits[[self.blank, self.eot]] = -np.inf         # openai/whisper SuppressBlank
            tok = int(np.argmax(logits))
            finite = logits[np.isfinite(logits)]
            logprob += float(logits[tok] - (finite.max() + np.log(np.exp(finite - finite.max()).sum())))
            n_scored += 1
            if tok == self.eot:
                break
            out.append(tok)
            cut = self._loop_start(out)
            if cut >= 0:                                         # decoding loop — keep one copy, stop
                out = out[:cut]
                break
            logits, k, v = self._decode([tok], k, v, cross_k, cross_v, offset)
            offset += 1
        self.last_avg_logprob = logprob / max(n_scored, 1)
        text = b"".join(self.vocab.get(t, b"") for t in out if t < self.eot).decode("utf-8", errors="ignore").strip()
        if self.last_avg_logprob < LOGPROB_THRESHOLD:
            return ""                                            # low-confidence guess (music, noise, tones)
        text = re.sub(r"[\[(]\s*(music|applause|laughter|silence|noise)\s*[\])]", " ", text, flags=re.I).strip()
        if not re.search(r"\p{L}[\p{L}\p{M}]+", text):
            return ""                                            # no real word — a stray glyph, not speech
        return text
