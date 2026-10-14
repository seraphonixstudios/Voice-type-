"""CUDA DLL bootstrap: make pip-installed NVIDIA libraries loadable.

The `nvidia-*-cu12` pip packages drop DLLs (cublas, cudnn, runtime) under
``site-packages/nvidia/*/bin``, but Windows only finds them via PATH or
``os.add_dll_directory`` — and environment changes don't reliably reach
already-running GUI processes. So the app registers every NVIDIA bin dir
itself at startup, before ctranslate2/faster-whisper ever loads.
Best-effort: never raises.
"""
from __future__ import annotations

import logging
import os
import site
import sys

log = logging.getLogger("voicetype.cuda")


def nvidia_bin_dirs() -> list[str]:
    found: list[str] = []
    search_roots: list[str] = []
    try:
        search_roots.extend(site.getsitepackages())
    except Exception:
        pass
    try:
        search_roots.append(site.getusersitepackages())
    except Exception:
        pass
    search_roots.extend(sys.path)
    seen: set[str] = set()
    for root in search_roots:
        nvidia = os.path.join(str(root), "nvidia")
        if nvidia in seen or not os.path.isdir(nvidia):
            continue
        seen.add(nvidia)
        try:
            for entry in sorted(os.listdir(nvidia)):
                bindir = os.path.join(nvidia, entry, "bin")
                if os.path.isdir(bindir) and bindir not in found:
                    found.append(bindir)
        except OSError:
            continue
    return found


def ensure_cuda_dlls() -> list[str]:
    """Register NVIDIA bin dirs for DLL loading. Returns dirs registered."""
    registered: list[str] = []
    for bindir in nvidia_bin_dirs():
        try:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(bindir)
        except Exception:
            pass
        try:
            if bindir not in os.environ.get("PATH", ""):
                os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
        except Exception:
            pass
        registered.append(bindir)
    if registered:
        log.info("registered CUDA DLL dirs: %s", "; ".join(registered))
    return registered
