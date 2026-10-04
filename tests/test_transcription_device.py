import sys
from types import SimpleNamespace

import pytest

from clipper.transcriber import transcription_device


@pytest.mark.parametrize("count,types,expected", [(0, {"float16"}, "cpu"),
    (1, {"float32"}, "cpu"), (1, {"float16", "float32"}, "cuda")])
def test_auto_checks_gpu_compute_support(monkeypatch, count, types, expected):
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(
        get_cuda_device_count=lambda: count, get_supported_compute_types=lambda device: types))
    assert transcription_device("auto") == expected


def test_auto_missing_cuda_runtime_falls_back(monkeypatch):
    def broken():
        raise RuntimeError("CUDA runtime unavailable")
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(get_cuda_device_count=broken))
    assert transcription_device("auto") == "cpu"
    assert transcription_device("cuda") == "cuda"
