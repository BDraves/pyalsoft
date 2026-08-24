"""Blocking waits for managed voice and stream state changes."""

from __future__ import annotations

from pyalsoft._managed._wait import _Waiter
from pyalsoft._managed.playback.session import Playback
from pyalsoft._managed.playback.streams import update_stream
from pyalsoft._managed.playback.voices import get_voice_status
from pyalsoft._managed.resources import Stream, StreamState, Voice, VoiceState


def wait(
    playback: Playback,
    voice: Voice | Stream,
    *,
    timeout: float | None = None,
    poll_interval: float = 0.01,
) -> bool:
    """Block until a voice or stream becomes terminal, or a timeout expires.

    This portable wait uses ordinary managed status queries and therefore does
    not require an optional OpenAL event extension. For streams it also reclaims
    processed buffers and advances the managed stream lifecycle.

    Args:
        playback: Session that owns ``voice``.
        voice: Static voice or stream to observe.
        timeout: Maximum wall-clock seconds to wait, or ``None`` for no limit.
        poll_interval: Positive wall-clock seconds between status queries.

    Returns:
        ``True`` when the resource is terminal, or ``False`` when the timeout
        expires first.

    Raises:
        TypeError: A handle or timing argument has the wrong type.
        ValueError: A timing argument is invalid.
        InvalidHandleError: The handle is released or belongs to another session.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot query the resource.
    """

    waiter = _Waiter(timeout, poll_interval)
    while True:
        if isinstance(voice, Stream):
            if update_stream(playback, voice).state in (
                StreamState.FINISHED,
                StreamState.STOPPED,
            ):
                return True
        elif isinstance(voice, Voice):
            if get_voice_status(playback, voice).state is VoiceState.STOPPED:
                return True
        else:
            raise TypeError("voice must be a Voice or Stream")
        if not waiter.pause():
            return False
