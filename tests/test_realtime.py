from classnote.realtime_live import parse_transcript_event


def test_parse_realtime_delta_and_completion() -> None:
    delta = parse_transcript_event(
        {
            "type": "conversation.item.input_audio_transcription.delta",
            "item_id": "item_1",
            "delta": "Hello",
        }
    )
    completed = parse_transcript_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item_1",
            "transcript": "Hello class.",
        }
    )
    assert delta is not None and delta.kind == "delta" and delta.text == "Hello"
    assert completed is not None and completed.kind == "completed"
    assert completed.text == "Hello class."


def test_parse_vad_timestamps_and_error() -> None:
    started = parse_transcript_event(
        {"type": "input_audio_buffer.speech_started", "item_id": "i", "audio_start_ms": 120}
    )
    stopped = parse_transcript_event(
        {"type": "input_audio_buffer.speech_stopped", "item_id": "i", "audio_end_ms": 980}
    )
    error = parse_transcript_event({"type": "error", "error": {"message": "bad audio"}})
    assert started is not None and started.audio_start_ms == 120
    assert stopped is not None and stopped.audio_end_ms == 980
    assert error is not None and error.message == "bad audio"


def test_unknown_realtime_event_is_ignored() -> None:
    assert parse_transcript_event({"type": "rate_limits.updated"}) is None
