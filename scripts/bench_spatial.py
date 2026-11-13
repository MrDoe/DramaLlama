"""Benchmark the spatial renderer: order, sources, reflections, motion.

Reports wall-clock seconds and render-seconds-per-audio-second so the mixer
can pick sensible defaults. Run: python scripts/bench_spatial.py
"""
import ctypes
import pathlib
import time

import numpy as np

LIB = pathlib.Path("build/spatial/libdrama_llama_spatial.so").resolve()
SR = 48000

lib = ctypes.CDLL(str(LIB))
lib.dl_create.restype = ctypes.c_void_p
lib.dl_create.argtypes = [ctypes.c_int] + [ctypes.c_int] * 3 + [ctypes.POINTER(ctypes.c_float)] * 2
lib.dl_set_sources.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
lib.dl_set_listener.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_float]
lib.dl_process.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
# Handles must be c_void_p: a missing argtype truncates the 64-bit pointer to a
# C int and dereferences garbage whenever the heap is above 4 GB.
lib.dl_get_n_sh.restype = ctypes.c_int
lib.dl_get_n_sh.argtypes = [ctypes.c_void_p]
lib.dl_get_frame_size.restype = ctypes.c_int
lib.dl_get_frame_size.argtypes = [ctypes.c_void_p]
lib.dl_destroy.argtypes = [ctypes.c_void_p]


def fp(a):
    """Return (contiguous float32 buffer, pointer); keep the buffer alive."""
    buf = np.ascontiguousarray(a, dtype=np.float32)
    return buf, buf.ctypes.data_as(ctypes.POINTER(ctypes.c_float))


room = (ctypes.c_float * 3)(8.0, 7.0, 3.5)
absorb = (ctypes.c_float * 6)(*([0.3] * 6))
DURATION = 10.0
BLOCK = SR // 10  # 100 ms render blocks


def bench(order, n_sources, reflections, move_every):
    h = lib.dl_create(SR, order, reflections, 1, room, absorb)
    nsh = lib.dl_get_n_sh(h)
    nblocks = int(DURATION * SR) // BLOCK

    rng = np.random.default_rng(1)
    full = rng.standard_normal((n_sources, nblocks * BLOCK), dtype=np.float32) * 0.1
    out = np.zeros((nsh, nblocks * BLOCK), dtype=np.float32)

    base = np.zeros((n_sources, 3), dtype=np.float32)
    for i in range(n_sources):
        base[i] = [1.0 + i * (6.0 / max(1, n_sources)), 5.0, 1.5]
    listener_buf, listener_ptr = fp([4.0, 1.0, 1.6])
    lib.dl_set_listener(h, listener_ptr, ctypes.c_float(0.0))
    base_buf, base_ptr = fp(base)
    lib.dl_set_sources(h, n_sources, base_ptr, None)

    scratch_in = np.zeros((n_sources, BLOCK), dtype=np.float32)
    scratch_out = np.zeros((nsh, BLOCK), dtype=np.float32)

    t0 = time.perf_counter()
    for b in range(nblocks):
        if move_every and b and b % move_every == 0:
            moved = base.copy()
            drift = (b / nblocks) * 1.5
            moved[:, 0] = np.clip(moved[:, 0] + drift, 0.2, 7.8)
            moved[:, 1] = np.clip(moved[:, 1] - drift, 0.2, 6.8)
            moved_buf, moved_ptr = fp(moved)
            lib.dl_set_sources(h, n_sources, moved_ptr, None)

        s = b * BLOCK
        scratch_in[:] = full[:, s:s + BLOCK]
        in_buf, in_ptr = fp(scratch_in)
        out_buf, out_ptr = fp(scratch_out)
        lib.dl_process(h, BLOCK, in_ptr, out_ptr)
        out[:, s:s + BLOCK] = out_buf

    wall = time.perf_counter() - t0
    lib.dl_destroy(h)
    return wall, wall / DURATION


print(f"{'order':>5} {'srcs':>5} {'refl':>5} {'blk/move':>9} {'wall s':>8} {'s/s':>7}")
for order, n_src, refl, mv in [
    (1, 2, 2, 0), (1, 6, 2, 0), (1, 6, 2, 5),
    (3, 2, 2, 0), (3, 6, 2, 0), (3, 6, 2, 5), (3, 6, 2, 1),
    (3, 12, 2, 5), (3, 6, 0, 5), (3, 6, 4, 5),
]:
    wall, ratio = bench(order, n_src, refl, mv)
    print(f"{order:>5} {n_src:>5} {refl:>5} {mv:>9} {wall:>8.2f} {ratio:>7.2f}")
