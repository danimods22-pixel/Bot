"""
Demangle C++ Itanium ABI pakai library `cxxfilt` (ctypes binding ke __cxa_demangle
dari libstdc++, sudah teruji & tersedia di hampir semua Linux) — BUKAN nge-port
ulang Demangler.java (915 baris parser custom) dari nol.

Yang diporting di sini murni "pemecah" hasil demangle penuh (mis.
"MyGame::Player::TakeDamage(int, bool) const") jadi (class_name, method_name,
parameters), setara DemangledSymbol.getClassName()/getMethodName()/getParameters()
di versi Java.
"""
import re

try:
    import cxxfilt
    _HAS_CXXFILT = True
except Exception:
    _HAS_CXXFILT = False

_libstdcxx = None
if not _HAS_CXXFILT:
    try:
        import ctypes
        import ctypes.util
        _path = ctypes.util.find_library("stdc++")
        if _path:
            _libstdcxx = ctypes.CDLL(_path)
            _libstdcxx.__cxa_demangle.restype = ctypes.c_void_p
            _libstdcxx.__cxa_demangle.argtypes = [
                ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    except Exception:
        _libstdcxx = None


def _cxa_demangle(mangled):
    """Fallback kalau paket `cxxfilt` gak ke-install: manggil __cxa_demangle langsung
    dari libstdc++ (fungsi C yang sama yang dipakai cxxfilt di baliknya) lewat ctypes."""
    if _libstdcxx is None:
        return None
    import ctypes
    status = ctypes.c_int(0)
    ptr = _libstdcxx.__cxa_demangle(mangled.encode("utf-8", "ignore"), None, None, ctypes.byref(status))
    if not ptr or status.value != 0:
        return None
    try:
        result = ctypes.cast(ptr, ctypes.c_char_p).value.decode("utf-8", "replace")
        return result
    finally:
        libc = ctypes.CDLL(None)
        libc.free(ptr)


class DemangledSymbol:
    __slots__ = ("class_name", "method_name", "parameters", "is_mangled", "readable")

    def __init__(self, class_name, method_name, parameters, is_mangled, readable):
        self.class_name = class_name
        self.method_name = method_name
        self.parameters = parameters
        self.is_mangled = is_mangled
        self.readable = readable

    def get_class_name(self):
        return self.class_name

    def get_method_name(self):
        return self.method_name

    def get_parameters(self):
        return self.parameters


def _clean_name(mangled):
    return mangled


def _unreadable(mangled):
    return DemangledSymbol("Global", _clean_name(mangled), "", True, False)


def _find_matching(s, open_i, open_ch, close_ch):
    """Cari index penutup yang matching untuk open_ch di posisi open_i (nested-aware)."""
    depth = 0
    i = open_i
    n = len(s)
    while i < n:
        c = s[i]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _find_top_level_paren(s):
    """Cari '(' pertama di top-level (di luar <...> nested) -> awal parameter list."""
    depth_angle = 0
    depth_paren_seen_operator = False
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == '<':
            depth_angle += 1
        elif c == '>':
            if depth_angle > 0:
                depth_angle -= 1
        elif c == '(' and depth_angle == 0:
            return i
        i += 1
    return -1


def _split_top_level_scopes(name_part):
    """Pecah 'A::B<C::D>::E' jadi ['A','B<C::D>','E'] — '::' di dalam <...> atau (...) diabaikan."""
    parts = []
    depth = 0
    buf = []
    i = 0
    n = len(name_part)
    while i < n:
        c = name_part[i]
        if c in "<(":
            depth += 1
            buf.append(c)
            i += 1
        elif c in ">)":
            if depth > 0:
                depth -= 1
            buf.append(c)
            i += 1
        elif depth == 0 and name_part[i:i + 2] == "::":
            parts.append("".join(buf))
            buf = []
            i += 2
        else:
            buf.append(c)
            i += 1
    parts.append("".join(buf))
    return parts


def split_demangled(full_signature, mangled_fallback):
    """full_signature: hasil cxxfilt.demangle() (mis. "Ns::Cls::Method(int) const").
    Return DemangledSymbol setara Java Demangler.demangle()."""
    if not full_signature:
        return _unreadable(mangled_fallback)

    s = full_signature.strip()
    paren = _find_top_level_paren(s)
    if paren == -1:
        # bukan function signature (data symbol dsb) -> fallback Global
        return DemangledSymbol("Global", _clean_name(mangled_fallback), "", True, False)

    name_part = s[:paren]
    close = _find_matching(s, paren, "(", ")")
    if close == -1:
        return _unreadable(mangled_fallback)
    params = s[paren + 1:close]
    tail = s[close + 1:].strip()  # " const" / " const volatile" dst.
    if tail:
        params = "%s /*%s*/" % (params, tail)

    name_part = name_part.strip()
    scopes = _split_top_level_scopes(name_part)
    if len(scopes) == 1:
        # free function, gak ada "::" -> gak masuk class manapun (setara "Global" & didrop
        # oleh NativeEngineDumper, tapi tetep dikembalikan biar caller yang mutusin)
        return DemangledSymbol("Global", scopes[0], params, True, True)

    method_name = scopes[-1]
    class_name = "::".join(scopes[:-1])
    if not class_name:
        return DemangledSymbol("Global", method_name, params, True, True)
    return DemangledSymbol(class_name, method_name, params, True, True)


def demangle(mangled):
    """Setara Demangler.demangle() versi Java."""
    if not mangled:
        return _unreadable("unnamed_function")
    if not mangled.startswith("_Z"):
        return DemangledSymbol("Global", _clean_name(mangled), "", False, True)
    full = None
    if _HAS_CXXFILT:
        try:
            full = cxxfilt.demangle(mangled)
        except Exception:
            full = None
    if full is None:
        full = _cxa_demangle(mangled)
    if full is None or full == mangled:
        return _unreadable(mangled)
    return split_demangled(full, mangled)
