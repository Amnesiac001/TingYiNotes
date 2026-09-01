import numpy as np

from classnote.live import AudioDevice, LiveAudioMonitor, analyze_audio_level, resample_pcm16


def test_audio_level_classifies_silence_low_good_and_clipping() -> None:
    timeline = np.linspace(0, 20 * np.pi, 48000, dtype=np.float32)
    assert analyze_audio_level(np.zeros(48000, dtype=np.float32)).status == "silent"
    assert analyze_audio_level(np.sin(timeline) * 0.01).status == "low"
    assert analyze_audio_level(np.sin(timeline) * 0.2).status == "good"
    assert analyze_audio_level(np.ones(48000, dtype=np.float32)).status == "clipping"


def test_audio_device_labels_make_microphone_and_system_sound_clear() -> None:
    microphone = AudioDevice(1, "Microphone Array", 1, 48000)
    loopback = AudioDevice(
        -1,
        "Speakers",
        2,
        48000,
        backend="soundcard-loopback",
        backend_id="speaker-id",
    )
    assert microphone.label == "麦克风 · Microphone Array"
    assert loopback.label == "系统声音 · Speakers"
    assert loopback.is_loopback


def test_live_audio_monitor_reports_sustained_silence_signal_and_drops() -> None:
    now = [100.0]
    events: list[tuple[str, object]] = []
    monitor = LiveAudioMonitor(
        lambda name, payload: events.append((name, payload)),
        interval_seconds=0,
        clock=lambda: now[0],
    )
    silence = np.zeros(480, dtype=np.int16).tobytes()
    signal = (np.sin(np.linspace(0, 8 * np.pi, 480)) * 9000).astype(np.int16).tobytes()

    monitor.observe(silence)
    now[0] += 9.5
    monitor.observe(silence, dropped_ms=60)
    now[0] += 0.1
    monitor.observe(signal, dropped_ms=60)

    metrics = [payload for name, payload in events if name == "audio_metrics"]
    assert metrics[0]["silent_seconds"] == 0
    assert metrics[1]["silent_seconds"] == 9.5
    assert metrics[1]["dropped_ms"] == 60
    assert metrics[2]["has_signal"] is True
    assert metrics[2]["silent_seconds"] == 0


def test_resample_pcm16_converts_native_microphone_rate_to_whisper_rate() -> None:
    source = np.arange(1440, dtype=np.int16)
    converted = np.frombuffer(resample_pcm16(source.tobytes(), 48000, 16000), dtype=np.int16)

    assert converted.size == 480
    assert converted[:4].tolist() == [1, 4, 7, 10]


def test_resample_pcm16_supports_non_integer_rates_and_identity() -> None:
    source = np.arange(1323, dtype=np.int16).tobytes()
    assert len(resample_pcm16(source, 44100, 16000)) == 480 * 2
    assert resample_pcm16(source, 16000, 16000) == source
