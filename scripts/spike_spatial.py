"""M0 spike: verify the SAF room sim and the stage coordinate convention.

Checks, in order:
  1. direct-path arrival time matches physics (3.43 m -> 10 ms at 343 m/s);
  2. a source straight ahead of the listener decodes centred;
  3. a source to the listener's left decodes left-biased;
  4. rotating the listener to face that source recentres it.

Stage convention (shared with SPARTA AmbiRoomSim):
  x = width 0..W, +x is the listener's LEFT at default facing
  y = depth 0..D, the front wall is at y = 0
  z = height, facing azimuth: 0 faces the front wall (-y), +90 faces +x

Run directly:  python scripts/spike_spatial.py
"""
import ctypes
import pathlib

import numpy as np

LIB = pathlib.Path("build/spatial/libdrama_llama_spatial.so").resolve()
SR = 48000
SPEED_OF_SOUND = 343.0
DIST = 3.43

lib = ctypes.CDLL(str(LIB))
lib.dl_create.restype = ctypes.c_void_p
lib.dl_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                          ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
lib.dl_destroy.argtypes = [ctypes.c_void_p]
lib.dl_set_sources.argtypes = [ctypes.c_void_p, ctypes.c_int,
                               ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
lib.dl_set_listener.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_float]
lib.dl_set_listener_facing.argtypes = [ctypes.c_void_p, ctypes.c_float]
lib.dl_process.argtypes = [ctypes.c_void_p, ctypes.c_int,
                           ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
# Every handle argument needs an argtype. Without it ctypes passes the pointer
# as a C int, truncating it to 32 bits; that only segfaults when the heap sits
# above 4 GB (it did on the ubuntu-22.04 CI runner, not on the dev box).
lib.dl_get_n_sh.restype = ctypes.c_int
lib.dl_get_n_sh.argtypes = [ctypes.c_void_p]
lib.dl_get_frame_size.restype = ctypes.c_int
lib.dl_get_frame_size.argtypes = [ctypes.c_void_p]
lib.dl_decode_stereo.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                 ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
lib.dl_last_error.restype = ctypes.c_char_p
lib.dl_last_error.argtypes = [ctypes.c_void_p]


def f32(arr):
    return np.asarray(arr, dtype=np.float32)


def fp(arr):
    """Return (contiguous float32 buffer, pointer). Keep the buffer alive for the call."""
    buf = np.ascontiguousarray(arr, dtype=np.float32)
    return buf, buf.ctypes.data_as(ctypes.POINTER(ctypes.c_float))


room = (ctypes.c_float * 3)(6.0, 5.0, 3.0)
absorb = (ctypes.c_float * 6)(*([0.25] * 6))

# Reflections OFF so the first arrival is purely the direct path.
h = lib.dl_create(SR, 1, 2, 0, room, absorb)
assert h, "dl_create failed"

nsh = lib.dl_get_n_sh(h)
fs = lib.dl_get_frame_size(h)
print(f"n_sh={nsh} frame_size={fs}")
assert nsh == 4, f"order-1 expected 4 SH channels, got {nsh}"

LISTENER = [3.0, 2.5, 1.5]
N = (4800 // fs) * fs


def render(src_xyz, facing=0.0):
    """Feed one click, return the decoded stereo block."""
    lbuf, lptr = fp(LISTENER)
    lib.dl_set_listener(h, lptr, ctypes.c_float(facing))
    sbuf, sptr = fp(src_xyz)
    lib.dl_set_sources(h, 1, sptr, None)

    inbuf = np.zeros((1, N), dtype=np.float32)
    sh = np.zeros((nsh, N), dtype=np.float32)
    inbuf[0, 0] = 1.0
    in_ptr = inbuf.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    sh_ptr = sh.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    rc = lib.dl_process(h, N, in_ptr, sh_ptr)
    assert rc == 0, f"dl_process failed: {lib.dl_last_error(h)}"

    stereo = np.zeros((2, N), dtype=np.float32)
    st_ptr = stereo.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    rc = lib.dl_decode_stereo(h, N, sh_ptr, st_ptr)
    assert rc == 0, f"dl_decode_stereo failed: {lib.dl_last_error(h)}"

    energy = np.sqrt((sh ** 2).sum(axis=0))
    onset = int(np.argmax(energy > energy.max() * 0.05))
    lf, rf = float(np.abs(stereo[0]).max()), float(np.abs(stereo[1]).max())
    return onset, lf, rf, (lf - rf) / (lf + rf)


# 1. physics: direct path delay
onset, lf, rf, bal = render([LISTENER[0], LISTENER[1] - DIST, LISTENER[2]])
expected = int(DIST / SPEED_OF_SOUND * SR)
print(f"[1] onset={onset} ({onset/SR*1000:.2f} ms), expected ~{expected} ({expected/SR*1000:.2f} ms)")
assert abs(onset - expected) < SR * 0.003, f"direct path off by {abs(onset-expected)} samples"
assert lf > 0 and rf > 0, "stereo decode produced silence"

# 2. straight ahead (front wall is -y) decodes centred
onset, lf, rf, bal = render([LISTENER[0], LISTENER[1] - DIST, LISTENER[2]])
print(f"[2] front   L={lf:.4f} R={rf:.4f} balance={bal:+.3f} (expect ~0)")
assert abs(bal) < 0.15, f"front source not centred: {bal}"

# 3. to the left (+x) decodes left-biased
onset, lf, rf, bal = render([LISTENER[0] + DIST, LISTENER[1], LISTENER[2]])
print(f"[3] left    L={lf:.4f} R={rf:.4f} balance={bal:+.3f} (expect > +0.3)")
assert bal > 0.3, f"left source not left-biased: {bal}"

# 3b. to the right (-x) decodes right-biased
onset, lf, rf, bal = render([LISTENER[0] - DIST, LISTENER[1], LISTENER[2]])
print(f"[3b] right  L={lf:.4f} R={rf:.4f} balance={bal:+.3f} (expect < -0.3)")
assert bal < -0.3, f"right source not right-biased: {bal}"

# 4. turn the listener left by 90 deg: the left source is now straight ahead
onset, lf, rf, bal = render([LISTENER[0] + DIST, LISTENER[1], LISTENER[2]], facing=90.0)
print(f"[4] left, facing +90  balance={bal:+.3f} (expect ~0)")
assert abs(bal) < 0.20, f"listener rotation did not recentre: {bal}"

lib.dl_destroy(h)
print("\nM0 spike PASSED: timing, centring, panning and listener rotation all correct.")
