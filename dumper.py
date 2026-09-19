"""
Port of Dumper.java — generator dump.cs.
Isi: // Image, // Dll, // Namespace, class/struct/enum/interface, fields (+offset),
methods (+RVA/Offset/VA/Slot), parameter (+default value), GenericInstMethod, custom attribute.
"""
import struct
from .custom_attr import CustomAttributeDataReader

TYPE_STRING = {
    1: "void", 2: "bool", 3: "char", 4: "sbyte", 5: "byte", 6: "short",
    7: "ushort", 8: "int", 9: "uint", 10: "long", 11: "ulong", 12: "float",
    13: "double", 14: "string", 22: "TypedReference", 24: "IntPtr",
    25: "UIntPtr", 28: "object",
}

MAX_GENERIC_TYPE_ARGS = 64


def _hex_u64(v):
    return format(v & 0xFFFFFFFFFFFFFFFF, "x").upper()


def _hex_u32(v):
    return format(v & 0xFFFFFFFF, "x").upper()


def _join(items, sep=", "):
    return sep.join(items)


class Dumper:
    def __init__(self, metadata, il2cpp):
        self.metadata = metadata
        self.il2cpp = il2cpp
        self._method_modifiers = {}

        self.totalExpected = 0
        self.dumpedOk = 0
        self.failedCount = 0
        self.report = []

    # ================= entry point =================

    def dump(self, out_path):
        with open(out_path, "w", encoding="utf-8", newline="\n") as w:
            for image_index, image_def in enumerate(self.metadata.imageDefs):
                w.write("// Image %d: %s - %d\n" % (
                    image_index, self.metadata.get_string_from_index(image_def.nameIndex), image_def.typeStart))
            for image_def in self.metadata.imageDefs:
                try:
                    image_name = self.metadata.get_string_from_index(image_def.nameIndex)
                except Exception:
                    image_name = "image#%d" % self.totalExpected
                type_end = image_def.typeStart + image_def.typeCount
                self.totalExpected += image_def.typeCount
                for type_def_index in range(image_def.typeStart, type_end):
                    try:
                        self._dump_type(w, image_def, image_name, type_def_index)
                        self.dumpedOk += 1
                    except Exception as e:
                        self.failedCount += 1
                        name = self._safe_type_name(type_def_index)
                        self.report.append("%s | typeDefIndex=%d | %s | %s" % (image_name, type_def_index, name, e))
                        w.write("\n/* [dumper] class typeDefIndex=%d (%s) error: %s */\n}\n" % (type_def_index, name, e))

    def _safe_type_name(self, type_def_index):
        try:
            t = self.metadata.typeDefs[type_def_index]
            n = self.metadata.get_string_from_index(t.nameIndex)
            ns = self.metadata.get_string_from_index(t.namespaceIndex)
            return (ns + "." if ns else "") + n
        except Exception:
            return "(nama tidak terbaca)"

    # ================= per-class =================

    def _dump_type(self, w, image_def, image_name, type_def_index):
        metadata = self.metadata
        il2cpp = self.il2cpp
        type_def = metadata.typeDefs[type_def_index]
        extends_list = []
        if 0 <= type_def.parentIndex < len(il2cpp.types):
            parent = il2cpp.types[type_def.parentIndex]
            parent_name = self.get_type_name(parent, False, False)
            if not type_def.is_value_type() and not type_def.is_enum() and parent_name != "object":
                extends_list.append(parent_name)
        if type_def.interfaces_count > 0:
            for i in range(type_def.interfaces_count):
                idx = type_def.interfacesStart + i
                if idx < 0 or idx >= len(metadata.interfaceIndices):
                    break
                t_idx = metadata.interfaceIndices[idx]
                if t_idx < 0 or t_idx >= len(il2cpp.types):
                    break
                extends_list.append(self.get_type_name(il2cpp.types[t_idx], False, False))

        w.write("\n// Dll : %s\n" % image_name)
        w.write("// Namespace: %s\n" % metadata.get_string_from_index(type_def.namespaceIndex))
        w.write(self._get_custom_attribute(image_def, type_def.customAttributeIndex, type_def.token, ""))
        if type_def.flags & 0x2000:
            w.write("[Serializable]\n")
        visibility = type_def.flags & 0x00000007
        if visibility in (1, 2):
            w.write("public ")
        elif visibility in (0, 4, 3):
            w.write("internal ")
        elif visibility == 5:
            w.write("private ")
        elif visibility == 6:
            w.write("protected ")
        elif visibility == 7:
            w.write("protected internal ")

        if (type_def.flags & 0x80) and (type_def.flags & 0x100):
            w.write("static ")
        elif not (type_def.flags & 0x20) and (type_def.flags & 0x80):
            w.write("abstract ")
        elif not type_def.is_value_type() and not type_def.is_enum() and (type_def.flags & 0x100):
            w.write("sealed ")
        if type_def.flags & 0x20:
            w.write("interface ")
        elif type_def.is_enum():
            w.write("enum ")
        elif type_def.is_value_type():
            w.write("struct ")
        else:
            w.write("class ")

        w.write(self.get_type_def_name(type_def, False, True))
        if extends_list:
            w.write(" : " + _join(extends_list))
        w.write(" // TypeDefIndex: %d\n{\n" % type_def_index)

        # ===== Fields =====
        if type_def.field_count > 0:
            w.write("\n\t// Fields\n")
            field_end = type_def.fieldStart + type_def.field_count
            for i in range(type_def.fieldStart, field_end):
                if i < 0 or i >= len(metadata.fieldDefs):
                    break
                field_def = metadata.fieldDefs[i]
                try:
                    field_type = il2cpp.types[field_def.typeIndex] if 0 <= field_def.typeIndex < len(il2cpp.types) else None
                    if field_type is None:
                        w.write("\t// field type invalid\n")
                        continue
                    w.write(self._get_custom_attribute(image_def, field_def.customAttributeIndex, field_def.token, "\t"))
                    is_static = False
                    is_const = False
                    w.write("\t")
                    access = field_type.attrs & 0x0007
                    if access == 1:
                        w.write("private ")
                    elif access == 6:
                        w.write("public ")
                    elif access == 4:
                        w.write("protected ")
                    elif access in (2, 5):
                        w.write("internal ")
                    elif access == 7:
                        w.write("protected internal ")
                    if field_type.attrs & 0x40:
                        is_const = True
                        w.write("const ")
                    else:
                        if field_type.attrs & 0x10:
                            is_static = True
                            w.write("static ")
                        if field_type.attrs & 0x20:
                            w.write("readonly ")
                    w.write(self.get_type_name(field_type, False, False) + " "
                            + metadata.get_string_from_index(field_def.nameIndex))
                    dv = metadata.fieldDefaultValueDic.get(i)
                    if dv is not None and dv.dataIndex != -1:
                        value = self.try_get_default_value(dv.typeIndex, dv.dataIndex)
                        if value is not None:
                            w.write(" = ")
                            self._write_value(w, value)
                        else:
                            w.write(" /*Metadata offset 0x%s*/" % format(dv.dataIndex, "x").upper())
                    if not is_const:
                        try:
                            off = il2cpp.get_field_offset_from_index(
                                type_def_index, i - type_def.fieldStart, i, type_def.is_value_type(), is_static)
                            w.write("; // 0x%s\n" % _hex_u32(off))
                        except Exception:
                            w.write(";\n")
                    else:
                        w.write(";\n")
                except Exception as e:
                    w.write("\t// field %s could not be decoded: %s\n" % (self._safe_string(field_def.nameIndex), e))

        # ===== Methods =====
        if type_def.method_count > 0:
            w.write("\n\t// Methods\n")
            method_end = type_def.methodStart + type_def.method_count
            for i in range(type_def.methodStart, method_end):
                if i < 0 or i >= len(metadata.methodDefs):
                    break
                w.write("\n")
                method_def = metadata.methodDefs[i]
                try:
                    is_abstract = bool(method_def.flags & 0x400)
                    w.write(self._get_custom_attribute(image_def, method_def.customAttributeIndex, method_def.token, "\t"))
                    method_pointer = 0
                    try:
                        method_pointer = il2cpp.get_method_pointer(image_name, method_def)
                    except Exception:
                        pass
                    if not is_abstract and method_pointer > 0:
                        fixed = il2cpp.get_rva(method_pointer)
                        w.write("\t// RVA: 0x%s Offset: 0x%s VA: 0x%s" % (
                            _hex_u64(fixed), _hex_u64(self._map_vat_safe(method_pointer)), _hex_u64(method_pointer)))
                    else:
                        w.write("\t// RVA: -1 Offset: -1")
                    if method_def.slot != 0xFFFF:
                        w.write(" Slot: %d" % method_def.slot)
                    w.write("\n")

                    w.write("\t" + self._get_modifiers(method_def, i))
                    if method_def.returnType < 0 or method_def.returnType >= len(il2cpp.types):
                        w.write("void " + metadata.get_string_from_index(method_def.nameIndex) + "(")
                    else:
                        ret_type = il2cpp.types[method_def.returnType]
                        method_name = metadata.get_string_from_index(method_def.nameIndex)
                        if 0 <= method_def.genericContainerIndex < len(metadata.genericContainers):
                            gc = metadata.genericContainers[method_def.genericContainerIndex]
                            method_name += self.get_generic_container_params(gc)
                        if ret_type.byref == 1:
                            w.write("ref ")
                        w.write(self.get_type_name(ret_type, False, False) + " " + method_name + "(")

                    param_strs = []
                    for j in range(method_def.parameterCount):
                        p_idx = method_def.parameterStart + j
                        if p_idx < 0 or p_idx >= len(metadata.parameterDefs):
                            break
                        parameter_def = metadata.parameterDefs[p_idx]
                        sb = []
                        if parameter_def.typeIndex < 0 or parameter_def.typeIndex >= len(il2cpp.types):
                            sb.append("object " + metadata.get_string_from_index(parameter_def.nameIndex))
                            param_strs.append("".join(sb))
                            continue
                        p_type = il2cpp.types[parameter_def.typeIndex]
                        if p_type.byref == 1:
                            if (p_type.attrs & 0x2) and not (p_type.attrs & 0x1):
                                sb.append("out ")
                            elif not (p_type.attrs & 0x2) and (p_type.attrs & 0x1):
                                sb.append("in ")
                            else:
                                sb.append("ref ")
                        else:
                            if p_type.attrs & 0x1:
                                sb.append("[In] ")
                            if p_type.attrs & 0x2:
                                sb.append("[Out] ")
                        sb.append(self.get_type_name(p_type, False, False) + " "
                                   + metadata.get_string_from_index(parameter_def.nameIndex))
                        pdv = metadata.paramDefaultValueDic.get(p_idx)
                        if pdv is not None and pdv.dataIndex != -1:
                            value = self.try_get_default_value(pdv.typeIndex, pdv.dataIndex)
                            if value is not None:
                                sb.append(" = ")
                                sb.append(self._format_value_inline(value))
                        param_strs.append("".join(sb))
                    w.write(_join(param_strs))
                    if is_abstract:
                        w.write(");\n")
                    else:
                        w.write(") { }\n")

                    # GenericInstMethod
                    specs = il2cpp.methodDefMethodSpecs.get(i)
                    if specs:
                        w.write("\t/* GenericInstMethod :\n")
                        groups = {}
                        order = []
                        for spec in specs:
                            ptr = il2cpp.methodSpecGenericMethodPointers.get(spec.key(), 0)
                            if ptr not in groups:
                                groups[ptr] = []
                                order.append(ptr)
                            groups[ptr].append(spec)
                        for ptr in order:
                            w.write("\t|\n")
                            if ptr > 0:
                                fixed = il2cpp.get_rva(ptr)
                                w.write("\t|-RVA: 0x%s Offset: 0x%s VA: 0x%s\n" % (
                                    _hex_u64(fixed), _hex_u64(self._map_vat_safe(ptr)), _hex_u64(ptr)))
                            else:
                                w.write("\t|-RVA: -1 Offset: -1\n")
                            for spec in groups[ptr]:
                                nm = self.get_method_spec_name(spec)
                                if nm is not None:
                                    w.write("\t|-%s.%s\n" % (nm[0], nm[1]))
                        w.write("\t*/\n")
                except Exception as e:
                    w.write("\t// method %s could not be decoded: %s\n" % (self._safe_string(method_def.nameIndex), e))
        w.write("}\n")

    # ================= custom attribute =================

    def _get_custom_attribute(self, image_def, custom_attribute_index, token, padding):
        try:
            metadata = self.metadata
            if metadata.version < 21:
                return ""
            if metadata.version < 29:
                range_index = metadata.get_legacy_custom_attribute_index(image_def, custom_attribute_index, int(token))
                type_idxs = metadata.get_legacy_attribute_type_indices(range_index)
                out = []
                for attribute_type_index in type_idxs:
                    if attribute_type_index < 0 or attribute_type_index >= len(self.il2cpp.types):
                        continue
                    name = self.get_type_name(self.il2cpp.types[attribute_type_index], False, False)
                    if not name or name == "object":
                        continue
                    if name.endswith("Attribute") and len(name) > len("Attribute"):
                        name = name[: -len("Attribute")]
                    out.append("%s[%s]\n" % (padding, name))
                return "".join(out)
            else:
                attr_index = metadata.get_custom_attribute_index_v29(image_def, token)
                if attr_index < 0:
                    return ""
                blob = metadata.read_attribute_data_blob(attr_index)
                if not blob:
                    return ""
                reader = CustomAttributeDataReader(
                    metadata,
                    lambda type_index: self._resolve_type_index_name(type_index),
                    lambda enum_type_index: self._resolve_enum_underlying_type_byte(enum_type_index),
                    blob)
                if reader.count == 0:
                    return ""
                out = []
                for _ in range(reader.count):
                    out.append("%s%s\n" % (padding, reader.get_string_custom_attribute_data()))
                return "".join(out)
        except Exception:
            return ""

    def _resolve_type_index_name(self, type_index):
        try:
            if type_index < 0 or type_index >= len(self.il2cpp.types):
                return None
            return self.get_type_name(self.il2cpp.types[type_index], False, False)
        except Exception:
            return None

    def _resolve_enum_underlying_type_byte(self, enum_type_index):
        if enum_type_index < 0 or enum_type_index >= len(self.il2cpp.types):
            raise ValueError("index tipe enum tidak valid: %d" % enum_type_index)
        enum_type = self.il2cpp.types[enum_type_index]
        type_def = self.get_type_def_from_type(enum_type)
        if type_def is None:
            raise ValueError("typeDef untuk enum tidak ditemukan")
        element_type_index = type_def.elementTypeIndex
        if element_type_index < 0 or element_type_index >= len(self.il2cpp.types):
            raise ValueError("elementTypeIndex enum tidak valid")
        return self.il2cpp.types[element_type_index].type

    def _map_vat_safe(self, va):
        try:
            return self.il2cpp.map_va_to_offset(va)
        except Exception:
            return 0

    # ================= modifiers =================

    def _get_modifiers(self, method_def, index):
        cached = self._method_modifiers.get(index)
        if cached is not None:
            return cached
        out = []
        access = method_def.flags & 0x0007
        if access == 1:
            out.append("private ")
        elif access == 6:
            out.append("public ")
        elif access == 4:
            out.append("protected ")
        elif access in (2, 3):
            out.append("internal ")
        elif access == 7:
            out.append("protected internal ")
        if method_def.flags & 0x10:
            out.append("static ")
        if method_def.flags & 0x400:
            out.append("abstract ")
            if (method_def.flags & 0x00F0) == 0x0010:
                out.append("override ")
        elif method_def.flags & 0x20:
            if (method_def.flags & 0x00F0) == 0x0010:
                out.append("sealed override ")
        elif method_def.flags & 0x40:
            if (method_def.flags & 0x0100) == 0x0100:
                out.append("virtual ")
            else:
                out.append("override ")
        if method_def.flags & 0x2000:
            out.append("extern ")
        s = "".join(out)
        self._method_modifiers[index] = s
        return s

    # ================= type names =================

    def get_type_name(self, t, add_namespace, is_nested):
        metadata = self.metadata
        il2cpp = self.il2cpp
        typ = t.type
        if typ == 0x14:  # ARRAY
            off = il2cpp.map_va_to_offset(t.data)
            il2cpp.bin.seek(off)
            etype = il2cpp._read_ptr()
            rank = il2cpp.bin.read_u8()
            if rank < 1:
                rank = 1
            el = self.get_type_name(self.get_il2cpp_type(etype), add_namespace, False)
            return el + "[" + ("," * (rank - 1)) + "]"
        if typ == 0x1D:  # SZARRAY
            return self.get_type_name(self.get_il2cpp_type(t.data), add_namespace, False) + "[]"
        if typ == 0x0F:  # PTR
            return self.get_type_name(self.get_il2cpp_type(t.data), add_namespace, False) + "*"
        if typ in (0x13, 0x1E):  # VAR / MVAR
            idx = int(t.data)
            if 0 <= idx < len(metadata.genericParameters):
                return metadata.get_string_from_index(metadata.genericParameters[idx].nameIndex)
            return "T%d" % idx
        if typ in (0x11, 0x12):  # VALUETYPE / CLASS
            td = self.get_type_def_from_type(t)
            if td is None:
                return "object"
            s = ""
            if td.declaringTypeIndex != -1 and td.declaringTypeIndex < len(il2cpp.types):
                s = self.get_type_name(il2cpp.types[td.declaringTypeIndex], add_namespace, True) + "."
            elif add_namespace:
                ns = metadata.get_string_from_index(td.namespaceIndex)
                if ns:
                    s = ns + "."
            type_name = metadata.get_string_from_index(td.nameIndex)
            bi = type_name.find("`")
            s += type_name[:bi] if bi != -1 else type_name
            if is_nested:
                return s
            if 0 <= td.genericContainerIndex < len(metadata.genericContainers):
                s += self.get_generic_container_params(metadata.genericContainers[td.genericContainerIndex])
            return s
        if typ == 0x15:  # GENERICINST
            off = il2cpp.map_va_to_offset(t.data)
            il2cpp.bin.seek(off)
            type_definition_index_or_type = il2cpp._read_ptr()
            class_inst = il2cpp._read_ptr()
            method_inst = il2cpp._read_ptr()
            if il2cpp.version >= 27:
                it = self.get_il2cpp_type(type_definition_index_or_type)
                type_def = self.get_type_def_from_type(it)
            else:
                idx = type_definition_index_or_type
                if idx == 0xFFFFFFFF or idx == -1:
                    return "object"
                type_def = metadata.typeDefs[int(idx)]
            if type_def is None:
                return "object"
            s = ""
            if type_def.declaringTypeIndex != -1 and type_def.declaringTypeIndex < len(il2cpp.types):
                s = self.get_type_name(il2cpp.types[type_def.declaringTypeIndex], add_namespace, True) + "."
            elif add_namespace:
                ns = metadata.get_string_from_index(type_def.namespaceIndex)
                if ns:
                    s = ns + "."
            type_name = metadata.get_string_from_index(type_def.nameIndex)
            bi = type_name.find("`")
            s += type_name[:bi] if bi != -1 else type_name
            if is_nested:
                return s
            if class_inst != 0:
                ginst_off = il2cpp.map_va_to_offset(class_inst)
                il2cpp.bin.seek(ginst_off)
                argc = il2cpp._read_ptr()
                argv = il2cpp._read_ptr()
                type_ptrs = il2cpp.read_ptr_arr(argv, int(argc))
                names = [self.get_type_name(self.get_il2cpp_type(p), add_namespace, False) for p in type_ptrs]
                s += "<" + _join(names) + ">"
            return s
        return TYPE_STRING.get(typ, "object")

    def get_type_def_from_type(self, t):
        try:
            idx = int(t.data)
            if idx < 0 or idx >= len(self.metadata.typeDefs):
                return None
            return self.metadata.typeDefs[idx]
        except Exception:
            return None

    def get_il2cpp_type(self, va):
        from .il2cpp import Il2CppType
        off = self.il2cpp.map_va_to_offset(va)
        self.il2cpp.bin.seek(off)
        t = Il2CppType()
        t.data = self.il2cpp.bin.read_u64() if self.il2cpp.is64 else self.il2cpp.bin.read_u32()
        t.bits = self.il2cpp.bin.read_u32()
        t.init(self.il2cpp.version)
        return t

    def get_type_def_name(self, type_def, add_namespace, generic_parameter):
        metadata = self.metadata
        il2cpp = self.il2cpp
        prefix = ""
        if type_def.declaringTypeIndex != -1 and type_def.declaringTypeIndex < len(il2cpp.types):
            prefix = self.get_type_name(il2cpp.types[type_def.declaringTypeIndex], add_namespace, True) + "."
        elif add_namespace:
            ns = metadata.get_string_from_index(type_def.namespaceIndex)
            if ns:
                prefix = ns + "."
        type_name = metadata.get_string_from_index(type_def.nameIndex)
        if 0 <= type_def.genericContainerIndex < len(metadata.genericContainers):
            index = type_name.find("`")
            if index != -1:
                type_name = type_name[:index]
            if generic_parameter:
                type_name += self.get_generic_container_params(metadata.genericContainers[type_def.genericContainerIndex])
        return prefix + type_name

    def get_generic_container_params(self, container):
        metadata = self.metadata
        names = []
        start = container.genericParameterStart
        for i in range(container.type_argc):
            if start + i >= len(metadata.genericParameters):
                break
            names.append(metadata.get_string_from_index(metadata.genericParameters[start + i].nameIndex))
        return "<" + _join(names) + ">"

    def get_method_spec_name(self, method_spec):
        try:
            metadata = self.metadata
            if method_spec.methodDefinitionIndex < 0 or method_spec.methodDefinitionIndex >= len(metadata.methodDefs):
                return None
            method_def = metadata.methodDefs[method_spec.methodDefinitionIndex]
            declaring_type = method_def.declaringType
            if declaring_type < 0 or declaring_type >= len(metadata.typeDefs):
                return None
            type_def = metadata.typeDefs[declaring_type]
            type_name = self.get_type_def_name(type_def, False, False)
            if method_spec.classIndexIndex != -1:
                type_name += self.get_generic_inst_params(self._get_generic_inst(method_spec.classIndexIndex))
            method_name = metadata.get_string_from_index(method_def.nameIndex)
            if method_spec.methodIndexIndex != -1:
                method_name += self.get_generic_inst_params(self._get_generic_inst(method_spec.methodIndexIndex))
            return (type_name, method_name)
        except Exception:
            return None

    def _get_generic_inst(self, index):
        gi = self.il2cpp.genericInsts
        if not gi or index < 0 or index >= len(gi):
            return None
        return gi[index]

    def get_generic_inst_params(self, inst):
        if inst is None or inst.typeArgc <= 0 or inst.typeArgc > MAX_GENERIC_TYPE_ARGS or inst.typeArgv == 0:
            return ""
        names = []
        pointers = self.il2cpp.read_ptr_arr(inst.typeArgv, inst.typeArgc)
        for p in pointers:
            try:
                t = self.get_il2cpp_type(p)
                names.append(self.get_type_name(t, False, False))
            except Exception:
                names.append("object")
        return "<" + _join(names) + ">"

    # ================= default values =================

    def try_get_default_value(self, type_index, data_index):
        try:
            metadata = self.metadata
            pointer = metadata.header.fieldAndParameterDefaultValueDataOffset + data_index
            t = self.il2cpp.types[type_index]
            metadata.bin.seek(pointer)
            b = metadata.bin
            typ = t.type
            if typ == 0x02:
                return b.read_u8() != 0
            if typ == 0x05:
                return b.read_u8()
            if typ == 0x04:
                v = b.read_u8()
                return v - 256 if v >= 128 else v
            if typ == 0x03:
                x = b.read_bytes(2)
                return chr(x[0] | (x[1] << 8))
            if typ == 0x07:
                return b.read_u16()
            if typ == 0x06:
                v = b.read_u16()
                return v - 65536 if v >= 32768 else v
            if typ == 0x09:
                return self._read_compressed_u32(b) if self.il2cpp.version >= 29 else b.read_u32()
            if typ == 0x08:
                return self._read_compressed_i32(b) if self.il2cpp.version >= 29 else b.read_i32()
            if typ == 0x0B:
                return b.read_u64()
            if typ == 0x0A:
                v = b.read_u64()
                return v - (1 << 64) if v >= (1 << 63) else v
            if typ == 0x0C:
                return struct.unpack("<f", struct.pack("<I", b.read_u32() & 0xFFFFFFFF))[0]
            if typ == 0x0D:
                return struct.unpack("<d", struct.pack("<Q", b.read_u64() & 0xFFFFFFFFFFFFFFFF))[0]
            if typ == 0x0E:
                if self.il2cpp.version >= 29:
                    length = self._read_compressed_i32(b)
                    if length == -1:
                        return None
                    x = b.read_bytes(length)
                    return x.decode("utf-8", errors="replace")
                else:
                    length = b.read_i32()
                    x = b.read_bytes(length)
                    return x.decode("utf-8", errors="replace")
            return None
        except Exception:
            return None

    def _read_compressed_u32(self, b):
        first = b.read_u8()
        if (first & 0x80) == 0:
            return first
        if first == 0xE8:
            return 0
        if (first & 0x40) == 0:
            return ((first & 0x7F) << 8) | b.read_u8()
        return ((first & 0x3F) << 24) | (b.read_u8() << 16) | (b.read_u8() << 8) | b.read_u8()

    def _read_compressed_i32(self, b):
        first = b.read_u8()
        if (first & 0x80) == 0:
            v = first
            return (v >> 1) | ~0x7F if (v & 0x1) == 1 else (v >> 1)
        if (first & 0x40) == 0:
            v = ((first & 0x7F) << 8) | b.read_u8()
            return (v >> 1) | ~0x3FFF if (v & 0x1) == 1 else (v >> 1)
        v = ((first & 0x3F) << 24) | (b.read_u8() << 16) | (b.read_u8() << 8) | b.read_u8()
        return (v >> 1) | ~0x1FFFFFFF if (v & 0x1) == 1 else (v >> 1)

    # ================= value formatting =================

    def _write_value(self, w, value):
        w.write(self._format_value_inline(value))

    def _format_value_inline(self, value):
        if isinstance(value, str):
            if len(value) == 1:
                # nilai CHAR direpresentasikan sebagai python str 1-karakter (lihat try_get_default_value tipe 0x03)
                return "'\\x%x'" % ord(value)
            return '"%s"' % self._escape(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            return self._format_number(repr(value))
        return str(value)

    @staticmethod
    def _format_number(py_repr):
        if py_repr.endswith(".0"):
            return py_repr[:-2]
        return py_repr

    @staticmethod
    def _escape(s):
        return (s.replace("\\", "\\\\").replace('"', '\\"')
                 .replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t"))

    def _safe_string(self, index):
        try:
            return self.metadata.get_string_from_index(index)
        except Exception:
            return "(unknown)"
