from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def no_network_or_devices(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Offline tests must not use network or audio hardware.")

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def local_only(sock, address):
        # Windows asyncio creates a loopback socketpair for its event-loop wakeup.
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return original_connect(sock, address)
        return forbidden()

    def local_only_ex(sock, address):
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return original_connect_ex(sock, address)
        return forbidden()

    monkeypatch.setattr(socket.socket, "connect", local_only)
    monkeypatch.setattr(socket.socket, "connect_ex", local_only_ex)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    import sounddevice

    monkeypatch.setattr(sounddevice, "RawInputStream", forbidden)
    monkeypatch.setattr(sounddevice, "RawOutputStream", forbidden)


def signal(amplitude=2000, seconds=0.02, rate=16000, *, noise=False, seed=1):
    count = round(seconds * rate)
    if noise:
        samples = np.random.default_rng(seed).normal(0, amplitude, count)
    else:
        samples = np.sin(np.arange(count) * 2 * np.pi * 220 / rate) * amplitude
    return np.clip(samples, -32768, 32767).astype("<i2").tobytes()


def echo_signal(pcm: bytes, delay_frames=320):
    return b"\0" * (delay_frames * 2) + pcm


class AsyncItems:
    def __init__(self, items):
        self.items = items
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True

    def __aiter__(self):
        return self.iterate()

    async def iterate(self):
        for item in self.items:
            if isinstance(item, Exception):
                raise item
            await asyncio.sleep(0)
            yield item

    def iter_bytes(self, chunk_size):
        assert chunk_size == 1024
        return self.iterate()


class AsyncMethod:
    def __init__(self, result=None):
        self.result = result
        self.calls = []
        self.errors = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.errors:
            raise self.errors.pop(0)
        return self.result


class FakeConnection:
    def __init__(self):
        self.session = SimpleNamespace(update=AsyncMethod())
        self.response = SimpleNamespace(create=AsyncMethod(), cancel=AsyncMethod())
        self.input_audio_buffer = SimpleNamespace(
            clear=AsyncMethod(), append=AsyncMethod(), commit=AsyncMethod()
        )
        self.conversation = SimpleNamespace(
            item=SimpleNamespace(
                create=AsyncMethod(), delete=AsyncMethod(), truncate=AsyncMethod()
            )
        )
        self.events = asyncio.Queue()

    async def recv(self):
        return await self.events.get()

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.events.get()


async def pump(playback, stop):
    while not stop.is_set():
        playback.callback(bytearray(960), 480, None, None)
        await asyncio.sleep(0.001)
