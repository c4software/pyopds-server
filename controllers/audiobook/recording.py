"""Normalization of the reference recordings of the cloned voices, with the standard library only (no
ffmpeg in the Docker image): PCM WAV in, 16-bit mono WAV at 24 kHz out, DC offset removed, silences cut
at both ends, level set (RMS target, peak limited) and short fades. A cloning model copies the level,
the room and the silences of its reference: a clean, even recording gives a clean voice.
"""
import array
import io
import math
import sys
import wave
from dataclasses import dataclass

# Output of Qwen3-TTS models: their reference recordings at the same rate
RATE = 24000
TARGET_RMS_DBFS = -20.0
PEAK_DBFS = -1.0
# A 20 ms window quieter than this (relative to the loudest one) is silence
SILENCE_BELOW_DB = 40.0
WINDOW_MS = 20
# Silence kept around the speech, fades at both ends
PAD_MS = 150
FADE_MS = 10
# Reference lengths that clone well
ADVISED_SECONDS = (3.0, 20.0)


@dataclass(frozen=True)
class Normalized:
    """A normalized recording: its WAV bytes, its length, and the gain applied (dB)."""
    wav: bytes
    seconds: float
    gain_db: float


def read_samples(data: bytes) -> tuple[list[float], int]:
    """(mono samples between -1 and 1, sample rate) of a PCM WAV file (8, 16, 24 or 32 bits, any channels).

    Raises:
        wave.Error, EOFError: When the data is not a PCM WAV file.
    """
    with wave.open(io.BytesIO(data)) as recording:
        channels, width, rate = recording.getnchannels(), recording.getsampwidth(), recording.getframerate()
        frames = recording.readframes(recording.getnframes())
    if width == 1:
        values = [byte - 128 for byte in frames]
        scale = 128.0
    elif width in (2, 4):
        values = array.array('h' if width == 2 else 'i', frames)
        if sys.byteorder == 'big':
            values.byteswap()
        scale = float(1 << (8 * width - 1))
    elif width == 3:
        values = [int.from_bytes(frames[i:i + 3], 'little', signed=True) for i in range(0, len(frames), 3)]
        scale = float(1 << 23)
    else:
        raise wave.Error(f'unsupported sample width: {width} bytes')
    if channels == 1:
        return [value / scale for value in values], rate
    return [sum(values[i:i + channels]) / (channels * scale) for i in range(0, len(values) - channels + 1, channels)], rate


def resample(samples: list[float], rate: int, target: int) -> list[float]:
    """Samples at the `target` rate (linear interpolation; enough for speech)."""
    if rate == target or not samples:
        return list(samples)
    count = int(len(samples) * target / rate)
    step = rate / target
    out = []
    for i in range(count):
        position = i * step
        left = int(position)
        right = min(left + 1, len(samples) - 1)
        fraction = position - left
        out.append(samples[left] * (1 - fraction) + samples[right] * fraction)
    return out


def trim_silence(samples: list[float], rate: int) -> list[float]:
    """Samples without the silences at both ends (PAD_MS kept around the speech)."""
    window = max(1, rate * WINDOW_MS // 1000)
    levels = [math.sqrt(sum(s * s for s in samples[i:i + window]) / len(samples[i:i + window]))
              for i in range(0, len(samples), window)]
    loudest = max(levels, default=0.0)
    if loudest <= 0:
        return []
    threshold = loudest * 10 ** (-SILENCE_BELOW_DB / 20)
    voiced = [i for i, level in enumerate(levels) if level >= threshold]
    pad = rate * PAD_MS // 1000
    start = max(0, voiced[0] * window - pad)
    end = min(len(samples), (voiced[-1] + 1) * window + pad)
    return samples[start:end]


def normalize(data: bytes) -> Normalized:
    """Normalized reference recording of a PCM WAV file (see the module docstring).

    Raises:
        wave.Error, EOFError: When the data is not a PCM WAV file.
        ValueError: When the recording is silent.
    """
    samples, rate = read_samples(data)
    if samples:
        offset = sum(samples) / len(samples)
        samples = [s - offset for s in samples]
    samples = trim_silence(resample(samples, rate, RATE), RATE)
    if not samples:
        raise ValueError('the recording is silent')
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    peak = max(abs(s) for s in samples)
    gain = min(10 ** (TARGET_RMS_DBFS / 20) / rms, 10 ** (PEAK_DBFS / 20) / peak)
    fade = min(len(samples) // 2, RATE * FADE_MS // 1000)
    out = array.array('h', bytes(2 * len(samples)))
    for i, sample in enumerate(samples):
        ramp = min(1.0, i / fade, (len(samples) - 1 - i) / fade) if fade else 1.0
        out[i] = max(-32767, min(32767, round(sample * gain * ramp * 32767)))
    if sys.byteorder == 'big':
        out.byteswap()
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(RATE)
        recording.writeframes(out.tobytes())
    return Normalized(buffer.getvalue(), len(samples) / RATE, 20 * math.log10(gain))


__all__ = ['ADVISED_SECONDS', 'Normalized', 'RATE', 'normalize', 'read_samples', 'resample', 'trim_silence']
