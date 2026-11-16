"""Validated runtime bytecode patches for native hip-fire sprinting in BL2.

The stable patch removes only the main-hand Weapon.IsFiring() exclusion from
CanContinueSprinting. The opt-in experimental patch additionally removes the
same exclusion from CanSprint, preserving every other native gate. Neither
patch changes game files on disk.
"""
from __future__ import annotations

import ctypes
import struct
import sys

import unrealsdk


CAN_CONTINUE_SPRINTING = "WillowGame.WillowPlayerPawn:CanContinueSprinting"
CAN_SPRINT = "WillowGame.WillowPlayerPawn:CanSprint"
BL2_SCRIPT_TARRAY_OFFSET = 0x54

# v0.15.8 captured the exact live Script sizes on the user's BL2/unrealsdk build.
# They are larger than the serialized UE Explorer streams because runtime object
# references expand from 4 to 8 bytes.
RUNTIME_SIZES = {
    CAN_CONTINUE_SPRINTING: 347,
    CAN_SPRINT: 365,
}

# Immediately before the main-hand IsFiring() context expression in both live
# functions. This sequence is identical in the two runtime captures.
TARGET_PREFIX = bytes.fromhex("84 72 50 03 00 00 00 2A 16 18 3E 00 81 84")
TARGET_SUFFIX = bytes.fromhex("18 1C 00 19 50")

# Runtime form of:
#   Context(LocalVariable Weapon, ..., VirtualFunction IsFiring())
# UObject/property refs are 8 bytes in the live Script; the FName is already 8.
RUNTIME_EXPR_LEN = 27
ORIGINAL_FIXED = {
    0: 0x19,  # EX_Context
    1: 0x50,  # local-variable expression
    6: 0x0A,
    7: 0x00,
    16: 0x00, # context metadata terminator after expanded 8-byte reference
    17: 0x1B, # EX_VirtualFunction
    26: 0x16, # EX_EndFunctionParms
}

# Replace only IsFiring() with an exact-length native false expression:
#   AndAnd(false, EX_Skip(<padding and EFP>))
# The short-circuit means the padding is never executed, while the parent
# expression keeps exactly the same byte offsets.
PATCH_EXPR = bytes.fromhex("82 28 18 16 00") + (b"\x0B" * 21) + b"\x16"
if len(PATCH_EXPR) != RUNTIME_EXPR_LEN:
    raise RuntimeError("native hip-fire replacement expression length mismatch")


def _matches_sparse(buf: bytes, pos: int, fixed: dict[int, int]) -> bool:
    if pos < 0 or pos + max(fixed, default=0) >= len(buf):
        return False
    return all(buf[pos + rel] == value for rel, value in fixed.items())


def _find_target(script: bytes):
    """Accept one complete target envelope with validated original/patch bytes.

    Count *all* prefix+suffix envelopes before validating opcodes. An extra
    same-shape expression, even if its fixed bytes differ, is ambiguous and
    must not be silently ignored while patching the other candidate.
    """
    envelopes = []
    start = 0
    while True:
        prefix_at = script.find(TARGET_PREFIX, start)
        if prefix_at < 0:
            break
        expr_at = prefix_at + len(TARGET_PREFIX)
        suffix_at = expr_at + RUNTIME_EXPR_LEN
        if (suffix_at + len(TARGET_SUFFIX) <= len(script)
                and script[suffix_at:suffix_at + len(TARGET_SUFFIX)] == TARGET_SUFFIX):
            envelopes.append(expr_at)
        start = prefix_at + 1
    if len(envelopes) != 1:
        raise RuntimeError(
            "expected one unambiguous main-hand IsFiring envelope, found %d" % len(envelopes)
        )
    expr_at = envelopes[0]
    expression = script[expr_at:expr_at + RUNTIME_EXPR_LEN]
    if expression == PATCH_EXPR:
        return expr_at, True
    if not _matches_sparse(expression, 0, ORIGINAL_FIXED):
        raise RuntimeError("unrecognized main-hand IsFiring expression")
    return expr_at, False


class NativeHipFirePatch:
    """Own a validated CanContinueSprinting hip-fire patch only.

    CanSprint intentionally remains vanilla so firing cannot start a new sprint;
    this preserves weapon/posture state and lets Sprint Assist retry after firing.
    """

    def __init__(self):
        self.script_array_offset = None
        self.patched = False
        self.start_while_firing = False
        self.last_error = None
        # A failed page-protection restore means we cannot prove the process
        # memory is safe to touch again. Keep this state until game restart.
        self._protection_uncertain = False
        self._owned = []  # (path, script_ptr, expr_off, original_bytes)
        self._kernel32 = None
        self._process = None

    def _init_winapi(self):
        if sys.platform != "win32":
            raise RuntimeError("native hip-fire patch requires Windows")
        if ctypes.sizeof(ctypes.c_void_p) != 4:
            raise RuntimeError("expected BL2's 32-bit process")
        if self._kernel32 is not None:
            return

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = ctypes.c_void_p
        k32.ReadProcessMemory.argtypes = (
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        )
        k32.ReadProcessMemory.restype = ctypes.c_int
        k32.WriteProcessMemory.argtypes = (
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        )
        k32.WriteProcessMemory.restype = ctypes.c_int
        k32.VirtualProtect.argtypes = (
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        )
        k32.VirtualProtect.restype = ctypes.c_int
        k32.FlushInstructionCache.argtypes = (
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        )
        k32.FlushInstructionCache.restype = ctypes.c_int

        self._kernel32 = k32
        self._process = k32.GetCurrentProcess()

    def _read(self, address: int, size: int):
        if not address or size <= 0:
            return None
        buf = (ctypes.c_ubyte * size)()
        read = ctypes.c_size_t(0)
        ok = self._kernel32.ReadProcessMemory(
            self._process,
            ctypes.c_void_p(address),
            buf,
            size,
            ctypes.byref(read),
        )
        if not ok or read.value != size:
            return None
        return bytes(buf)

    def _write(self, address: int, data: bytes):
        if self._protection_uncertain:
            raise RuntimeError("native hip-fire page protection uncertain; restart required")
        size = len(data)
        old_protect = ctypes.c_uint32(0)
        if not self._kernel32.VirtualProtect(
            ctypes.c_void_p(address), size, 0x40, ctypes.byref(old_protect)
        ):
            raise RuntimeError("VirtualProtect setup failed")
        try:
            buf = (ctypes.c_ubyte * size).from_buffer_copy(data)
            written = ctypes.c_size_t(0)
            if not self._kernel32.WriteProcessMemory(
                self._process,
                ctypes.c_void_p(address),
                buf,
                size,
                ctypes.byref(written),
            ) or written.value != size:
                raise RuntimeError("WriteProcessMemory failed or wrote fewer bytes than requested")
            # No claim that interpreted UnrealScript requires an instruction
            # cache flush. An unsuccessful WinAPI call is still not success.
            if not self._kernel32.FlushInstructionCache(
                self._process, ctypes.c_void_p(address), size
            ):
                raise RuntimeError("FlushInstructionCache failed")
        finally:
            ignored = ctypes.c_uint32(0)
            try:
                restored = self._kernel32.VirtualProtect(
                    ctypes.c_void_p(address),
                    size,
                    old_protect.value,
                    ctypes.byref(ignored),
                )
            except Exception as exc:
                self._protection_uncertain = True
                raise RuntimeError(
                    "VirtualProtect final restore raised; page protection uncertain; "
                    "restart required"
                ) from exc
            if not restored:
                self._protection_uncertain = True
                raise RuntimeError(
                    "VirtualProtect final restore failed; page protection uncertain; "
                    "restart required"
                )

    def _read_function_script(self, path: str):
        fn = unrealsdk.find_object("Function", path)
        if fn is None:
            raise RuntimeError(path + " UFunction not found")
        base = int(fn._get_address())
        raw = self._read(base + BL2_SCRIPT_TARRAY_OFFSET, 12)
        if raw is None:
            raise RuntimeError(path + " Script TArray unreadable at +0x54")
        data_ptr, num, maximum = struct.unpack("<III", raw)
        expected = RUNTIME_SIZES[path]
        if (
            data_ptr < 0x10000
            or num != expected
            or maximum < num
            or maximum > 0x100000
        ):
            raise RuntimeError(
                "%s unexpected Script TArray: ptr=0x%X num=%d max=%d expected=%d"
                % (path, data_ptr, num, maximum, expected)
            )
        script = self._read(data_ptr, num)
        if script is None or script[0] != 0x0F or script[-1] != 0x53:
            raise RuntimeError(path + " live Script validation failed")
        return data_ptr, script

    def _locate_all(self, start_while_firing=False):
        located = []
        targets = ((CAN_CONTINUE_SPRINTING, CAN_SPRINT)
                   if start_while_firing else (CAN_CONTINUE_SPRINTING,))
        for path in targets:
            ptr, script = self._read_function_script(path)
            expr_off, already_patched = _find_target(script)
            located.append((path, ptr, script, expr_off, already_patched))
        return located

    def apply(self, *, start_while_firing=False):
        """Apply exact validated eligibility patches; rollback atomically."""
        start_while_firing = bool(start_while_firing)
        if self._protection_uncertain:
            self.last_error = 'native hip-fire page protection uncertain; restart required'
            return False
        if self.patched:
            return self.start_while_firing == start_while_firing
        if self._owned:
            self.last_error = 'previous hip-fire patch restoration is still pending'
            return False
        self.last_error = None
        try:
            self._init_winapi()
            # Preserve the original zero-argument continuation-only contract;
            # existing ownership mocks and downstream callers rely on it.
            located = (self._locate_all(True) if start_while_firing
                       else self._locate_all())
            if any(item[4] for item in located):
                raise RuntimeError("native hip-fire bytes already patched by another owner")

            for path, ptr, script, expr_off, _ in located:
                original = script[expr_off : expr_off + RUNTIME_EXPR_LEN]
                if not _matches_sparse(original, 0, ORIGINAL_FIXED):
                    raise RuntimeError(path + " main-hand IsFiring validation failed")
                # Record ownership before writing: verification itself may fail.
                self._owned.append((path, ptr, expr_off, original))
                self._write(ptr + expr_off, PATCH_EXPR)
                if self._read(ptr + expr_off, RUNTIME_EXPR_LEN) != PATCH_EXPR:
                    raise RuntimeError(path + " native hip-fire write verification failed")

            self.script_array_offset = BL2_SCRIPT_TARRAY_OFFSET
            self.start_while_firing = start_while_firing
            self.patched = True
            return True
        except Exception as exc:
            error = repr(exc)
            self.patched = False
            restored = self.restore()
            self.last_error = error if restored else error + '; rollback: ' + str(self.last_error)
            return False

    def restore(self):
        """Restore only exact patch bytes owned by this instance."""
        if self._protection_uncertain:
            self.patched = False
            self.last_error = 'native hip-fire page protection uncertain; restart required'
            return False
        if not self._owned:
            self.patched = False
            self.start_while_firing = False
            self.script_array_offset = None
            return True
        try:
            self._init_winapi()
            for path, ptr, expr_off, original in reversed(self._owned):
                live_ptr, _ = self._read_function_script(path)
                if live_ptr != ptr:
                    raise RuntimeError(path + ' Script generation changed; old address is not writable')
                current = self._read(ptr + expr_off, RUNTIME_EXPR_LEN)
                if current == PATCH_EXPR:
                    self._write(ptr + expr_off, original)
                    if self._read(ptr + expr_off, RUNTIME_EXPR_LEN) != original:
                        raise RuntimeError(path + " native hip-fire restore verification failed")
                elif current != original:
                    raise RuntimeError(
                        path + " native hip-fire bytes do not match the original or "
                        "owned patch (partial write or foreign edit); restart required"
                    )
            self._owned = []
            self.script_array_offset = None
            self.patched = False
            self.start_while_firing = False
            return True
        except Exception as exc:
            self.patched = False
            self.last_error = repr(exc)
            return False
