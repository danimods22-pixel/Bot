"""
Port of CustomAttributeDataReader.java — decode blob custom-attribute mentah
(metadata versi >= 29).
"""
import struct

MAX_ATTR_ARGS = 1024

TYPE_BOOLEAN = 0x02
TYPE_CHAR = 0x03
TYPE_I1 = 0x04
TYPE_U1 = 0x05
TYPE_I2 = 0x06
TYPE_U2 = 0x07
TYPE_I4 = 0x08
TYPE_U4 = 0x09
TYPE_I8 = 0x0A
TYPE_U8 = 0x0B
TYPE_R4 = 0x0C
TYPE_R8 = 0x0D
TYPE_STRING = 0x0E
TYPE_SZARRAY = 0x1D
TYPE_ENUM = 0x55
TYPE_INDEX = 0xFF


class _Named:
    __slots__ = ("declaring", "index")


class CustomAttributeDataReader:
    """type_resolver: fn(type_index) -> str|None.
    enum_resolver: fn(enum_type_index) -> int (Il2CppTypeEnum byte)."""

    def __init__(self, metadata, type_resolver, enum_resolver, data: bytes):
        self.metadata = metadata
        self.type_resolver = type_resolver
        self.enum_resolver = enum_resolver
        self.data = data
        self.pos = 0
        c = self._read_compressed_u32()
        if c < 0 or c > MAX_ATTR_ARGS:
            raise ValueError("jumlah attribute tidak masuk akal: %d" % c)
        self.count = int(c)
        self.ctor_buffer = self.pos
        self.data_buffer = self.ctor_buffer + self.count * 4
        if self.data_buffer > len(data):
            raise ValueError("attribute data buffer melebihi panjang blob")

    def _remaining(self):
        return max(0, len(self.data) - self.pos)

    def _u8(self):
        if self.pos >= len(self.data):
            raise EOFError("EOF pada attribute blob")
        v = self.data[self.pos]
        self.pos += 1
        return v

    def _i8(self):
        v = self._u8()
        return v - 256 if v >= 128 else v

    def _u16(self):
        a = self._u8(); b = self._u8()
        return a | (b << 8)

    def _i16(self):
        v = self._u16()
        return v - 65536 if v >= 32768 else v

    def _u32raw(self):
        a = self._u8(); b = self._u8(); c = self._u8(); d = self._u8()
        return a | (b << 8) | (c << 16) | (d << 24)

    def _u64raw(self):
        lo = self._u32raw()
        hi = self._u32raw()
        return (hi << 32) | lo

    def _f32(self):
        return struct.unpack("<f", struct.pack("<I", self._u32raw() & 0xFFFFFFFF))[0]

    def _f64(self):
        return struct.unpack("<d", struct.pack("<Q", self._u64raw() & 0xFFFFFFFFFFFFFFFF))[0]

    def _bytes(self, n):
        if n < 0 or n > self._remaining():
            raise ValueError("panjang data tidak valid: %d" % n)
        r = self.data[self.pos:self.pos + n]
        self.pos += n
        return r

    def _read_compressed_u32(self):
        first = self._u8()
        if (first & 0x80) == 0:
            return first
        if (first & 0x40) == 0:
            return ((first & 0x7F) << 8) | self._u8()
        if first == 0xE8:
            return 0  # quirk v39
        if first == 0xFF:
            return 0xFFFFFFFF
        return ((first & 0x3F) << 24) | (self._u8() << 16) | (self._u8() << 8) | self._u8()

    def _read_compressed_i32(self):
        if self.pos >= len(self.data):
            raise EOFError("EOF pada attribute blob (compressed i32)")
        first0 = self.data[self.pos]
        if (first0 & 0x80) == 0:
            first = self._u8()
            v = first
            return (v >> 1) | ~0x7F if (v & 0x1) == 1 else (v >> 1)
        if (first0 & 0x40) == 0:
            first = self._u8()
            v = ((first & 0x7F) << 8) | self._u8()
            return (v >> 1) | ~0x3FFF if (v & 0x1) == 1 else (v >> 1)
        first = self._u8()
        if first == 0xFF:
            return -1
        v = ((first & 0x3F) << 24) | (self._u8() << 16) | (self._u8() << 8) | self._u8()
        return (v >> 1) | ~0x1FFFFFFF if (v & 0x1) == 1 else (v >> 1)

    def _read_encoded_type_enum(self):
        typ = self._u8()  # RAW byte, bukan compressed
        if typ == TYPE_ENUM:
            enum_type_index = self._read_compressed_i32()
            if self.enum_resolver is None:
                raise ValueError("tidak ada resolver untuk underlying type enum")
            typ = self.enum_resolver(enum_type_index)
        return typ

    def _read_attribute_data_value(self):
        if self._remaining() == 0:
            raise EOFError("data attribute habis")
        type_byte = self._read_encoded_type_enum()
        return self._decode_value_for_type(type_byte)

    def _decode_value_for_type(self, type_byte):
        if type_byte == TYPE_BOOLEAN:
            return "true" if self._u8() != 0 else "false"
        if type_byte == TYPE_CHAR:
            v = self._u16()
            return "'\\x%x'" % v
        if type_byte == TYPE_I1:
            return str(self._i8())
        if type_byte == TYPE_U1:
            return str(self._u8())
        if type_byte == TYPE_I2:
            return str(self._i16())
        if type_byte == TYPE_U2:
            return str(self._u16())
        if type_byte == TYPE_I4:
            return str(self._read_compressed_i32())
        if type_byte == TYPE_U4:
            return str(self._read_compressed_u32())
        if type_byte == TYPE_I8:
            v = self._u64raw()
            if v >= 0x8000000000000000:
                v -= 0x10000000000000000
            return str(v)
        if type_byte == TYPE_U8:
            return str(self._u64raw())
        if type_byte == TYPE_R4:
            return _trim_float(repr(self._f32()))
        if type_byte == TYPE_R8:
            return _trim_float(repr(self._f64()))
        if type_byte == TYPE_STRING:
            length = self._read_compressed_i32()
            if length == -1:
                return "null"
            if length < 0 or length > self._remaining():
                raise ValueError("panjang string attribute tidak valid")
            b = self._bytes(length)
            s = b.decode("utf-8", errors="replace").replace("\\", "\\\\").replace('"', '\\"')
            return '"%s"' % s
        if type_byte == TYPE_SZARRAY:
            length = self._read_compressed_i32()
            if length == -1:
                return "null"
            if length < 0 or length > MAX_ATTR_ARGS:
                raise ValueError("panjang array attribute tidak valid")
            array_element_type = self._read_encoded_type_enum()
            elements_are_different = self._u8()
            items = []
            for _ in range(length):
                if self._remaining() == 0:
                    break
                elem_type = array_element_type
                if elements_are_different == 1:
                    elem_type = self._read_encoded_type_enum()
                items.append(self._decode_value_for_type(elem_type))
            return "new[] { %s }" % ", ".join(items)
        if type_byte == TYPE_INDEX:
            type_index = self._read_compressed_i32()
            if type_index == -1:
                return "null"
            name = self.type_resolver(type_index) if self.type_resolver else None
            return "typeof(%s)" % name if name else "typeof(/* type index %d */)" % type_index
        raise ValueError("tipe attribute tidak dikenal: 0x%x" % type_byte)

    def _read_named_argument_class_and_index(self, current):
        member_index = self._read_compressed_i32()
        n = _Named()
        if member_index >= 0:
            n.declaring = current
            n.index = member_index
            return n
        actual_index = -(member_index + 1)
        type_index = self._read_compressed_u32()
        type_defs = self.metadata.typeDefs
        declaring = type_defs[type_index] if (type_defs and 0 <= type_index < len(type_defs)) else current
        n.declaring = declaring
        n.index = actual_index
        return n

    def get_string_custom_attribute_data(self):
        if self._remaining() == 0:
            raise EOFError("tidak ada data attribute tersisa")

        self.pos = self.ctor_buffer
        ctor_index = self._u32raw()
        method_defs = self.metadata.methodDefs
        ctor = method_defs[ctor_index] if (method_defs and 0 <= ctor_index < len(method_defs)) else None
        self.ctor_buffer = self.pos

        self.pos = self.data_buffer
        argument_count = min(self._read_compressed_u32(), MAX_ATTR_ARGS)
        field_count = min(self._read_compressed_u32(), MAX_ATTR_ARGS)
        property_count = min(self._read_compressed_u32(), MAX_ATTR_ARGS)

        arg_list = []

        for _ in range(argument_count):
            if self._remaining() == 0:
                break
            try:
                arg_list.append(self._read_attribute_data_value())
            except Exception:
                break

        type_def = None
        if ctor is not None and self.metadata.typeDefs and 0 <= ctor.declaringType < len(self.metadata.typeDefs):
            type_def = self.metadata.typeDefs[ctor.declaringType]

        for _ in range(field_count):
            if self._remaining() == 0:
                break
            try:
                val = self._read_attribute_data_value()
            except Exception:
                val = ""
            named = None
            if type_def is not None:
                try:
                    n = self._read_named_argument_class_and_index(type_def)
                    field_idx = n.declaring.fieldStart + n.index
                    field_defs = self.metadata.fieldDefs
                    if field_defs and 0 <= field_idx < len(field_defs):
                        name = self.metadata.get_string_from_index(field_defs[field_idx].nameIndex)
                        named = "%s = %s" % (name, val)
                except Exception:
                    pass
            arg_list.append(named if named is not None else val)

        for _ in range(property_count):
            if self._remaining() == 0:
                break
            try:
                val = self._read_attribute_data_value()
            except Exception:
                val = ""
            named = None
            if type_def is not None:
                try:
                    n = self._read_named_argument_class_and_index(type_def)
                    prop_idx = n.declaring.propertyStart + n.index
                    prop_defs = self.metadata.propertyDefs
                    if prop_defs and 0 <= prop_idx < len(prop_defs):
                        name = self.metadata.get_string_from_index(prop_defs[prop_idx].nameIndex)
                        named = "%s = %s" % (name, val)
                except Exception:
                    pass
            arg_list.append(named if named is not None else val)

        self.data_buffer = self.pos

        type_name = "UnknownAttribute"
        if type_def is not None:
            try:
                type_name = self.metadata.get_string_from_index(type_def.nameIndex)
            except Exception:
                pass
        if type_name.endswith("Attribute") and len(type_name) > len("Attribute"):
            type_name = type_name[: -len("Attribute")]

        if not arg_list:
            return "[%s]" % type_name
        return "[%s(%s)]" % (type_name, ", ".join(arg_list))


def _trim_float(s):
    return s[:-2] if s.endswith(".0") else s
