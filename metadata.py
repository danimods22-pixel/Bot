"""
Port of Metadata.java — parser global-metadata.dat.
Mendukung versi 16-31 + v38/v39 (Unity 6, format section-table baru).
"""
from .bin_io import Bin

SANITY = 0xFAB11BAF


class Header:
    def __init__(self):
        for name in (
            "stringLiteralOffset stringLiteralSize stringLiteralDataOffset stringLiteralDataSize "
            "stringOffset stringSize eventsOffset eventsSize propertiesOffset propertiesSize "
            "methodsOffset methodsSize parameterDefaultValuesOffset parameterDefaultValuesSize "
            "fieldDefaultValuesOffset fieldDefaultValuesSize fieldAndParameterDefaultValueDataOffset "
            "fieldAndParameterDefaultValueDataSize fieldMarshaledSizesOffset fieldMarshaledSizesSize "
            "parametersOffset parametersSize fieldsOffset fieldsSize genericParametersOffset "
            "genericParametersSize genericParameterConstraintsOffset genericParameterConstraintsSize "
            "genericContainersOffset genericContainersSize nestedTypesOffset nestedTypesSize "
            "interfacesOffset interfacesSize vtableMethodsOffset vtableMethodsSize "
            "interfaceOffsetsOffset interfaceOffsetsSize typeDefinitionsOffset typeDefinitionsSize "
            "rgctxEntriesOffset rgctxEntriesCount imagesOffset imagesSize assembliesOffset assembliesSize "
            "metadataUsageListsOffset metadataUsageListsCount metadataUsagePairsOffset "
            "metadataUsagePairsCount fieldRefsOffset fieldRefsSize referencedAssembliesOffset "
            "referencedAssembliesSize attributesInfoOffset attributesInfoCount attributeTypesOffset "
            "attributeTypesCount attributeDataOffset attributeDataSize attributeDataRangeOffset "
            "attributeDataRangeSize unresolvedVirtualCallParameterTypesOffset "
            "unresolvedVirtualCallParameterTypesSize unresolvedVirtualCallParameterRangesOffset "
            "unresolvedVirtualCallParameterRangesSize windowsRuntimeTypeNamesOffset "
            "windowsRuntimeTypeNamesSize windowsRuntimeStringsOffset windowsRuntimeStringsSize "
            "exportedTypeDefinitionsOffset exportedTypeDefinitionsSize"
        ).split():
            setattr(self, name, 0)


class ImageDef:
    def __init__(self):
        self.nameIndex = self.assemblyIndex = self.typeStart = self.typeCount = 0
        self.entryPointIndex = self.token = 0
        self.customAttributeStart = self.customAttributeCount = 0


class TypeDef:
    def __init__(self):
        self.nameIndex = self.namespaceIndex = self.customAttributeIndex = -1
        self.byvalTypeIndex = self.byrefTypeIndex = 0
        self.declaringTypeIndex = self.parentIndex = self.elementTypeIndex = 0
        self.rgctxStartIndex = self.rgctxCount = 0
        self.genericContainerIndex = 0
        self.flags = 0
        self.fieldStart = self.methodStart = self.eventStart = self.propertyStart = 0
        self.nestedTypesStart = self.interfacesStart = self.vtableStart = self.interfaceOffsetsStart = 0
        self.method_count = self.property_count = self.field_count = self.event_count = 0
        self.nested_type_count = self.vtable_count = self.interfaces_count = self.interface_offsets_count = 0
        self.bitfield = self.token = 0

    def is_value_type(self):
        return (self.bitfield & 0x1) == 1

    def is_enum(self):
        return ((self.bitfield >> 1) & 0x1) == 1


class MethodDef:
    def __init__(self):
        self.nameIndex = self.declaringType = self.returnType = 0
        self.returnParameterToken = self.parameterStart = 0
        self.customAttributeIndex = -1
        self.genericContainerIndex = 0
        self.methodIndex = self.invokerIndex = self.delegateWrapperIndex = 0
        self.rgctxStartIndex = self.rgctxCount = 0
        self.token = self.flags = self.iflags = self.slot = self.parameterCount = 0


class ParamDef:
    def __init__(self):
        self.nameIndex = self.token = 0
        self.customAttributeIndex = -1
        self.typeIndex = 0


class FieldDef:
    def __init__(self):
        self.nameIndex = self.typeIndex = self.token = 0
        self.customAttributeIndex = -1


class PropDef:
    def __init__(self):
        self.nameIndex = self.get = self.set = self.attrs = self.token = 0
        self.customAttributeIndex = -1


class EventDef:
    def __init__(self):
        self.nameIndex = self.typeIndex = self.add = self.remove = self.raise_ = self.token = 0
        self.customAttributeIndex = -1


class GenericContainer:
    def __init__(self):
        self.ownerIndex = self.type_argc = self.is_method = self.genericParameterStart = 0


class GenericParameter:
    def __init__(self):
        self.ownerIndex = self.nameIndex = 0
        self.constraintsStart = self.constraintsCount = self.num = self.flags = 0


class DefaultVal:
    def __init__(self):
        self.index = self.typeIndex = self.dataIndex = 0


class _AttrTypeRange:
    def __init__(self):
        self.token = self.start = self.count = 0


class AttrDataRange:
    def __init__(self):
        self.token = self.startOffset = 0


# ---- section index buat metadata v38/v39 ----
SEC_STRING_LITERALS = 0
SEC_STRING_LITERAL_DATA = 1
SEC_STRINGS = 2
SEC_EVENTS = 3
SEC_PROPERTIES = 4
SEC_METHODS = 5
SEC_PARAM_DEFAULT_VALUES = 6
SEC_FIELD_DEFAULT_VALUES = 7
SEC_DEFAULT_VALUE_DATA = 8
SEC_FIELD_MARSHALED_SIZES = 9
SEC_PARAMETERS = 10
SEC_FIELDS = 11
SEC_GENERIC_PARAMETERS = 12
SEC_GENERIC_PARAM_CONSTRAINTS = 13
SEC_GENERIC_CONTAINERS = 14
SEC_NESTED_TYPES = 15
SEC_INTERFACES = 16
SEC_VTABLE_METHODS = 17
SEC_INTERFACE_OFFSETS = 18
SEC_TYPE_DEFINITIONS = 19
SEC_IMAGES = 20
SEC_ASSEMBLIES = 21
SEC_FIELD_REFS = 22
SEC_REFERENCED_ASSEMBLIES = 23
SEC_ATTRIBUTE_DATA = 24
SEC_ATTRIBUTE_DATA_RANGES = 25


class Section:
    def __init__(self):
        self.offset = self.size = self.count = 0


def _i32(x):
    """emulasi overflow int32 Java buat readI32 yang dipakai sebagai indeks bertanda."""
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x >= 0x80000000 else x


class Metadata:
    def __init__(self, path):
        self.bin = Bin(path, "rb")
        self.version = 0.0
        self.header = None
        self.imageDefs = []
        self.typeDefs = []
        self.methodDefs = []
        self.parameterDefs = []
        self.fieldDefs = []
        self.propertyDefs = []
        self.eventDefs = []
        self.genericContainers = []
        self.genericParameters = []
        self.nestedTypeIndices = []
        self.interfaceIndices = []
        self.metadataUsagesCount = 0

        self.fieldDefaultValueDic = {}
        self.paramDefaultValueDic = {}
        self._stringCache = {}
        self._attributeTypeRanges = []
        self._attributeTypes = []
        self.attributeDataRanges = []

        # v38/39 state
        self._sectionsV38 = None
        self._typeIdxSize = 4
        self._typeDefIdxSize = 4
        self._genericContainerIdxSize = 4
        self._paramIdxSize = 4

        b = self.bin
        sanity = b.read_u32()
        if sanity != SANITY:
            raise ValueError("Bukan global-metadata.dat valid (sanity 0x%x)" % sanity)
        ver = b.read_i32()
        if ver < 16 or ver > 31:
            if ver not in (38, 39):
                raise ValueError("Versi metadata [%d] tidak didukung" % ver)
        self.version = float(ver)
        if ver >= 38:
            self._init_v38(ver)
            return

        self.header = self._read_header(self.version)

        if ver == 24:
            if self.header.stringLiteralOffset == 264:
                self.version = 24.2
                self.header = self._read_header(self.version)
            else:
                self.imageDefs = self._read_images()
                for img in self.imageDefs:
                    if img.token != 1:
                        self.version = 24.1
                        self.header = self._read_header(self.version)
                        break

        self.imageDefs = self._read_images()
        if self.version == 24.2 and len(self.imageDefs) > 0 and self.header.assembliesSize / 68 < len(self.imageDefs):
            self.version = 24.4
            self.header = self._read_header(self.version)
        v241plus = (self.version == 24.1 and len(self.imageDefs) > 0
                    and self.header.assembliesSize / 64 == len(self.imageDefs))

        self.typeDefs = self._read_type_defs(24.4 if v241plus else self.version)
        self.methodDefs = self._read_method_defs(self.version)
        self.parameterDefs = self._read_param_defs()
        self.fieldDefs = self._read_field_defs()
        self._read_default_values()
        self.propertyDefs = self._read_prop_defs()
        self.interfaceIndices = self._read_int_arr(self.header.interfacesOffset, self.header.interfacesSize // 4)
        self.nestedTypeIndices = self._read_int_arr(self.header.nestedTypesOffset, self.header.nestedTypesSize // 4)
        self.eventDefs = self._read_event_defs()
        self.genericContainers = self._read_generic_containers()
        self.genericParameters = self._read_generic_parameters()
        self._read_custom_attributes()

        if 16 < self.version < 27:
            self.metadataUsagesCount = 0
            b.seek(self.header.metadataUsageListsOffset)
            for _ in range(int(self.header.metadataUsageListsCount)):
                b.read_u32()
                self.metadataUsagesCount += b.read_u32()

        if v241plus:
            self.version = 24.1

    # ================= header versi 16-31 =================

    def _read_header(self, v):
        h = Header()
        b = self.bin
        b.seek(8)
        h.stringLiteralOffset = b.read_u32(); h.stringLiteralSize = b.read_i32()
        h.stringLiteralDataOffset = b.read_u32(); h.stringLiteralDataSize = b.read_i32()
        h.stringOffset = b.read_u32(); h.stringSize = b.read_i32()
        h.eventsOffset = b.read_u32(); h.eventsSize = b.read_i32()
        h.propertiesOffset = b.read_u32(); h.propertiesSize = b.read_i32()
        h.methodsOffset = b.read_u32(); h.methodsSize = b.read_i32()
        h.parameterDefaultValuesOffset = b.read_u32(); h.parameterDefaultValuesSize = b.read_i32()
        h.fieldDefaultValuesOffset = b.read_u32(); h.fieldDefaultValuesSize = b.read_i32()
        h.fieldAndParameterDefaultValueDataOffset = b.read_u32(); h.fieldAndParameterDefaultValueDataSize = b.read_i32()
        h.fieldMarshaledSizesOffset = b.read_i32(); h.fieldMarshaledSizesSize = b.read_i32()
        h.parametersOffset = b.read_u32(); h.parametersSize = b.read_i32()
        h.fieldsOffset = b.read_u32(); h.fieldsSize = b.read_i32()
        h.genericParametersOffset = b.read_u32(); h.genericParametersSize = b.read_i32()
        h.genericParameterConstraintsOffset = b.read_u32(); h.genericParameterConstraintsSize = b.read_i32()
        h.genericContainersOffset = b.read_u32(); h.genericContainersSize = b.read_i32()
        h.nestedTypesOffset = b.read_u32(); h.nestedTypesSize = b.read_i32()
        h.interfacesOffset = b.read_u32(); h.interfacesSize = b.read_i32()
        h.vtableMethodsOffset = b.read_u32(); h.vtableMethodsSize = b.read_i32()
        h.interfaceOffsetsOffset = b.read_i32(); h.interfaceOffsetsSize = b.read_i32()
        h.typeDefinitionsOffset = b.read_u32(); h.typeDefinitionsSize = b.read_i32()
        if v <= 24.1:
            h.rgctxEntriesOffset = b.read_u32(); h.rgctxEntriesCount = b.read_i32()
        h.imagesOffset = b.read_u32(); h.imagesSize = b.read_i32()
        h.assembliesOffset = b.read_u32(); h.assembliesSize = b.read_i32()
        if 19 <= v <= 24.5:
            h.metadataUsageListsOffset = b.read_u32(); h.metadataUsageListsCount = b.read_i32()
            h.metadataUsagePairsOffset = b.read_u32(); h.metadataUsagePairsCount = b.read_i32()
        if v >= 19:
            h.fieldRefsOffset = b.read_u32(); h.fieldRefsSize = b.read_i32()
        if v >= 20:
            h.referencedAssembliesOffset = b.read_i32(); h.referencedAssembliesSize = b.read_i32()
        if 21 <= v <= 27.2:
            h.attributesInfoOffset = b.read_u32(); h.attributesInfoCount = b.read_i32()
            h.attributeTypesOffset = b.read_u32(); h.attributeTypesCount = b.read_i32()
        if v >= 29:
            h.attributeDataOffset = b.read_u32(); h.attributeDataSize = b.read_i32()
            h.attributeDataRangeOffset = b.read_u32(); h.attributeDataRangeSize = b.read_i32()
        if v >= 22:
            h.unresolvedVirtualCallParameterTypesOffset = b.read_i32(); h.unresolvedVirtualCallParameterTypesSize = b.read_i32()
            h.unresolvedVirtualCallParameterRangesOffset = b.read_i32(); h.unresolvedVirtualCallParameterRangesSize = b.read_i32()
        if v >= 23:
            h.windowsRuntimeTypeNamesOffset = b.read_i32(); h.windowsRuntimeTypeNamesSize = b.read_i32()
        if v >= 27:
            h.windowsRuntimeStringsOffset = b.read_i32(); h.windowsRuntimeStringsSize = b.read_i32()
        if v >= 24:
            h.exportedTypeDefinitionsOffset = b.read_i32(); h.exportedTypeDefinitionsSize = b.read_i32()
        return h

    def _read_images(self):
        out = []
        b = self.bin
        h = self.header
        b.seek(h.imagesOffset)
        end = h.imagesOffset + h.imagesSize
        while b.pos < end:
            d = ImageDef()
            d.nameIndex = b.read_u32()
            d.assemblyIndex = b.read_i32()
            d.typeStart = b.read_i32()
            d.typeCount = b.read_u32()
            if self.version >= 24:
                b.read_i32(); b.read_u32()
            d.entryPointIndex = b.read_i32()
            if self.version >= 19:
                d.token = b.read_u32()
            if self.version >= 24.1:
                d.customAttributeStart = b.read_i32(); d.customAttributeCount = b.read_u32()
            out.append(d)
        return out

    def _read_type_defs(self, v):
        out = []
        b = self.bin
        h = self.header
        b.seek(h.typeDefinitionsOffset)
        end = h.typeDefinitionsOffset + h.typeDefinitionsSize
        while b.pos < end:
            t = TypeDef()
            t.nameIndex = b.read_u32()
            t.namespaceIndex = b.read_u32()
            if v <= 24:
                t.customAttributeIndex = b.read_i32()
            t.byvalTypeIndex = b.read_i32()
            if v <= 24.5:
                t.byrefTypeIndex = b.read_i32()
            t.declaringTypeIndex = b.read_i32()
            t.parentIndex = b.read_i32()
            t.elementTypeIndex = b.read_i32()
            if v <= 24.1:
                t.rgctxStartIndex = b.read_i32(); t.rgctxCount = b.read_i32()
            t.genericContainerIndex = b.read_i32()
            if v <= 22:
                b.read_i32(); b.read_i32()
            if 21 <= v <= 22:
                b.read_i32(); b.read_i32()
            t.flags = b.read_u32()
            t.fieldStart = b.read_i32(); t.methodStart = b.read_i32()
            t.eventStart = b.read_i32(); t.propertyStart = b.read_i32()
            t.nestedTypesStart = b.read_i32(); t.interfacesStart = b.read_i32()
            t.vtableStart = b.read_i32(); t.interfaceOffsetsStart = b.read_i32()
            t.method_count = b.read_u16(); t.property_count = b.read_u16()
            t.field_count = b.read_u16(); t.event_count = b.read_u16()
            t.nested_type_count = b.read_u16(); t.vtable_count = b.read_u16()
            t.interfaces_count = b.read_u16(); t.interface_offsets_count = b.read_u16()
            t.bitfield = b.read_u32()
            if v >= 19:
                t.token = b.read_u32()
            out.append(t)
        return out

    def _read_method_defs(self, v):
        out = []
        b = self.bin
        h = self.header
        b.seek(h.methodsOffset)
        end = h.methodsOffset + h.methodsSize
        while b.pos < end:
            m = MethodDef()
            m.nameIndex = b.read_u32()
            m.declaringType = b.read_i32()
            m.returnType = b.read_i32()
            if v >= 31:
                m.returnParameterToken = b.read_i32()
            m.parameterStart = b.read_i32()
            if v <= 24:
                m.customAttributeIndex = b.read_i32()
            m.genericContainerIndex = b.read_i32()
            if v <= 24.1:
                m.methodIndex = b.read_i32()
                m.invokerIndex = b.read_i32()
                m.delegateWrapperIndex = b.read_i32()
                m.rgctxStartIndex = b.read_i32()
                m.rgctxCount = b.read_i32()
            m.token = b.read_u32()
            m.flags = b.read_u16(); m.iflags = b.read_u16()
            m.slot = b.read_u16(); m.parameterCount = b.read_u16()
            out.append(m)
        return out

    def _read_param_defs(self):
        out = []
        b = self.bin
        h = self.header
        b.seek(h.parametersOffset)
        end = h.parametersOffset + h.parametersSize
        while b.pos < end:
            p = ParamDef()
            p.nameIndex = b.read_u32()
            p.token = b.read_u32()
            if self.version <= 24:
                p.customAttributeIndex = b.read_i32()
            p.typeIndex = b.read_i32()
            out.append(p)
        return out

    def _read_field_defs(self):
        out = []
        b = self.bin
        h = self.header
        b.seek(h.fieldsOffset)
        end = h.fieldsOffset + h.fieldsSize
        while b.pos < end:
            f = FieldDef()
            f.customAttributeIndex = -1
            f.nameIndex = b.read_u32()
            f.typeIndex = b.read_i32()
            if self.version <= 24:
                f.customAttributeIndex = b.read_i32()
            if self.version >= 19:
                f.token = b.read_u32()
            out.append(f)
        return out

    def _read_custom_attributes(self):
        if self.version <= 20:
            return
        h = self.header
        b = self.bin
        if self.version < 29:
            if h.attributesInfoOffset <= 0 or h.attributesInfoCount <= 0 or \
               h.attributeTypesOffset <= 0 or h.attributeTypesCount <= 0:
                return
            range_size = 8 if self.version <= 24 else 12
            range_count = int(h.attributesInfoCount // range_size)
            if range_count <= 0:
                return
            self._attributeTypeRanges = [None] * range_count
            b.seek(h.attributesInfoOffset)
            for i in range(range_count):
                r = _AttrTypeRange()
                if self.version > 24:
                    r.token = b.read_u32()
                r.start = b.read_i32()
                r.count = b.read_i32()
                self._attributeTypeRanges[i] = r
            type_count = int(h.attributeTypesCount // 4)
            if type_count <= 0:
                return
            self._attributeTypes = self._read_int_arr(h.attributeTypesOffset, type_count)
        else:
            if h.attributeDataRangeOffset <= 0 or h.attributeDataRangeSize <= 0:
                return
            range_count = int(h.attributeDataRangeSize // 8)
            if range_count <= 0:
                return
            self.attributeDataRanges = [None] * range_count
            b.seek(h.attributeDataRangeOffset)
            for i in range(range_count):
                r = AttrDataRange()
                r.token = b.read_u32()
                r.startOffset = b.read_u32()
                self.attributeDataRanges[i] = r

    def get_custom_attribute_index_v29(self, image_def, token):
        if image_def is None or token == 0 or not self.attributeDataRanges:
            return -1
        end = image_def.customAttributeStart + image_def.customAttributeCount
        start = max(0, image_def.customAttributeStart)
        for i in range(start, min(end, len(self.attributeDataRanges))):
            if self.attributeDataRanges[i].token == (token & 0xFFFFFFFF):
                return i
        return -1

    def read_attribute_data_blob(self, attribute_index):
        if attribute_index < 0 or attribute_index >= len(self.attributeDataRanges):
            return None
        start_off = self.attributeDataRanges[attribute_index].startOffset
        if attribute_index + 1 < len(self.attributeDataRanges):
            end_off = self.attributeDataRanges[attribute_index + 1].startOffset
        else:
            end_off = self.header.attributeDataSize
        size = end_off - start_off
        if size <= 0 or size > 4_000_000:
            return None
        self.bin.seek(self.header.attributeDataOffset + start_off)
        return self.bin.read_bytes(int(size))

    def get_legacy_custom_attribute_index(self, image_def, custom_attribute_index, token):
        if self.version <= 24:
            return custom_attribute_index
        return self._find_attribute_range(image_def, token)

    def get_legacy_attribute_type_indices(self, range_index):
        return self._get_attribute_types_for_range(range_index)

    def get_field_attribute_type_indices(self, image_def, field_def):
        if not self._attributeTypeRanges or not self._attributeTypes or field_def is None:
            return []
        range_index = field_def.customAttributeIndex
        if self.version > 24:
            range_index = -1
            if image_def is not None and field_def.token != 0:
                end = image_def.customAttributeStart + image_def.customAttributeCount
                start = max(0, image_def.customAttributeStart)
                for i in range(start, min(end, len(self._attributeTypeRanges))):
                    if self._attributeTypeRanges[i].token == field_def.token:
                        range_index = i
                        break
        return self._get_attribute_types_for_range(range_index)

    def get_type_attribute_type_indices(self, image_def, type_def):
        if not self._attributeTypeRanges or not self._attributeTypes or type_def is None:
            return []
        range_index = type_def.customAttributeIndex
        if self.version > 24:
            range_index = self._find_attribute_range(image_def, type_def.token)
        return self._get_attribute_types_for_range(range_index)

    def _find_attribute_range(self, image_def, token):
        if image_def is None or token == 0:
            return -1
        end = image_def.customAttributeStart + image_def.customAttributeCount
        start = max(0, image_def.customAttributeStart)
        for i in range(start, min(end, len(self._attributeTypeRanges))):
            if self._attributeTypeRanges[i].token == token:
                return i
        return -1

    def _get_attribute_types_for_range(self, range_index):
        if range_index < 0 or range_index >= len(self._attributeTypeRanges):
            return []
        r = self._attributeTypeRanges[range_index]
        start = max(0, r.start)
        end = start + max(0, r.count)
        if start >= len(self._attributeTypes) or end <= start:
            return []
        end = min(end, len(self._attributeTypes))
        return list(self._attributeTypes[start:end])

    def _read_prop_defs(self):
        out = []
        b = self.bin
        h = self.header
        b.seek(h.propertiesOffset)
        end = h.propertiesOffset + h.propertiesSize
        while b.pos < end:
            p = PropDef()
            p.nameIndex = b.read_u32()
            p.get = b.read_i32()
            p.set = b.read_i32()
            p.attrs = b.read_u32()
            if self.version <= 24:
                p.customAttributeIndex = b.read_i32()
            if self.version >= 19:
                p.token = b.read_u32()
            out.append(p)
        return out

    def _read_event_defs(self):
        out = []
        b = self.bin
        h = self.header
        b.seek(h.eventsOffset)
        end = h.eventsOffset + h.eventsSize
        while b.pos < end:
            e = EventDef()
            e.nameIndex = b.read_u32()
            e.typeIndex = b.read_i32()
            e.add = b.read_i32()
            e.remove = b.read_i32()
            e.raise_ = b.read_i32()
            if self.version <= 24:
                e.customAttributeIndex = b.read_i32()
            if self.version >= 19:
                e.token = b.read_u32()
            out.append(e)
        return out

    def _read_generic_containers(self):
        out = []
        b = self.bin
        h = self.header
        b.seek(h.genericContainersOffset)
        end = h.genericContainersOffset + h.genericContainersSize
        while b.pos < end:
            g = GenericContainer()
            g.ownerIndex = b.read_i32()
            g.type_argc = b.read_i32()
            g.is_method = b.read_i32()
            g.genericParameterStart = b.read_i32()
            out.append(g)
        return out

    def _read_generic_parameters(self):
        out = []
        b = self.bin
        h = self.header
        b.seek(h.genericParametersOffset)
        end = h.genericParametersOffset + h.genericParametersSize
        while b.pos < end:
            g = GenericParameter()
            g.ownerIndex = b.read_i32()
            g.nameIndex = b.read_u32()
            g.constraintsStart = b.read_u16()
            g.constraintsCount = b.read_u16()
            g.num = b.read_u16()
            g.flags = b.read_u16()
            out.append(g)
        return out

    def _read_default_values(self):
        b = self.bin
        h = self.header
        b.seek(h.fieldDefaultValuesOffset)
        end = h.fieldDefaultValuesOffset + h.fieldDefaultValuesSize
        while b.pos < end:
            d = DefaultVal()
            d.index = b.read_i32(); d.typeIndex = b.read_i32(); d.dataIndex = b.read_i32()
            self.fieldDefaultValueDic[d.index] = d
        b.seek(h.parameterDefaultValuesOffset)
        end = h.parameterDefaultValuesOffset + h.parameterDefaultValuesSize
        while b.pos < end:
            d = DefaultVal()
            d.index = b.read_i32(); d.typeIndex = b.read_i32(); d.dataIndex = b.read_i32()
            self.paramDefaultValueDic[d.index] = d

    def _read_int_arr(self, offset, count):
        count = max(0, int(count))
        b = self.bin
        b.seek(offset)
        return [b.read_i32() for _ in range(count)]

    def get_string_from_index(self, index):
        if index < 0:
            return ""
        cached = self._stringCache.get(index)
        if cached is not None:
            return cached
        s = self.bin.read_string_to_null(self.header.stringOffset + index)
        if len(self._stringCache) < 200000:
            self._stringCache[index] = s
        return s

    def close(self):
        self.bin.close()

    # ================= v38/v39 (Unity 6) =================

    def _sec(self, idx):
        return self._sectionsV38[idx]

    @staticmethod
    def _index_size(count):
        if count < 0xFF:
            return 1
        if count < 0xFFFF:
            return 2
        return 4

    def _read_idx(self, size):
        b = self.bin
        if size == 4:
            return b.read_i32()
        if size == 2:
            v = b.read_u16()
            return -1 if v == 0xFFFF else v
        v = b.read_u8()
        return -1 if v == 0xFF else v

    def _read_idx_arr_v38(self, s, idx_size):
        n = min(s.count, 0x7FFFFFFF)
        self.bin.seek(s.offset)
        return [self._read_idx(idx_size) for _ in range(int(n))]

    def _init_v38(self, ver):
        b = self.bin
        b.seek(8)
        n_sec = 40 if ver >= 104 else 31
        self._sectionsV38 = []
        for _ in range(n_sec):
            sec = Section()
            sec.offset = b.read_u32()
            sec.size = b.read_u32()
            sec.count = b.read_u32()
            self._sectionsV38.append(sec)

        sec_images = self._sec(SEC_IMAGES)
        sec_typedefs = self._sec(SEC_TYPE_DEFINITIONS)
        sec_methods = self._sec(SEC_METHODS)
        sec_params = self._sec(SEC_PARAMETERS)
        sec_fields = self._sec(SEC_FIELDS)
        sec_generic_containers = self._sec(SEC_GENERIC_CONTAINERS)
        sec_iface_offsets = self._sec(SEC_INTERFACE_OFFSETS)

        self._typeDefIdxSize = self._index_size(sec_typedefs.count)
        self._genericContainerIdxSize = self._index_size(sec_generic_containers.count)
        ifc_pair_size = 8 if sec_iface_offsets.count == 0 else sec_iface_offsets.size / sec_iface_offsets.count
        if ifc_pair_size == 8:
            self._typeIdxSize = 4
        elif ifc_pair_size == 6:
            self._typeIdxSize = 2
        elif ifc_pair_size == 5:
            self._typeIdxSize = 1
        else:
            self._typeIdxSize = 4
        if ver >= 39:
            self._paramIdxSize = self._index_size(sec_params.count)

        h = Header()
        h.stringOffset = self._sec(SEC_STRINGS).offset; h.stringSize = self._sec(SEC_STRINGS).size
        h.stringLiteralOffset = self._sec(SEC_STRING_LITERALS).offset; h.stringLiteralSize = self._sec(SEC_STRING_LITERALS).size
        h.stringLiteralDataOffset = self._sec(SEC_STRING_LITERAL_DATA).offset; h.stringLiteralDataSize = self._sec(SEC_STRING_LITERAL_DATA).size
        h.eventsOffset = self._sec(SEC_EVENTS).offset; h.eventsSize = self._sec(SEC_EVENTS).size
        h.propertiesOffset = self._sec(SEC_PROPERTIES).offset; h.propertiesSize = self._sec(SEC_PROPERTIES).size
        h.methodsOffset = sec_methods.offset; h.methodsSize = sec_methods.size
        h.parameterDefaultValuesOffset = self._sec(SEC_PARAM_DEFAULT_VALUES).offset; h.parameterDefaultValuesSize = self._sec(SEC_PARAM_DEFAULT_VALUES).size
        h.fieldDefaultValuesOffset = self._sec(SEC_FIELD_DEFAULT_VALUES).offset; h.fieldDefaultValuesSize = self._sec(SEC_FIELD_DEFAULT_VALUES).size
        h.fieldAndParameterDefaultValueDataOffset = self._sec(SEC_DEFAULT_VALUE_DATA).offset
        h.fieldAndParameterDefaultValueDataSize = self._sec(SEC_DEFAULT_VALUE_DATA).size
        h.parametersOffset = sec_params.offset; h.parametersSize = sec_params.size
        h.fieldsOffset = sec_fields.offset; h.fieldsSize = sec_fields.size
        h.genericParametersOffset = self._sec(SEC_GENERIC_PARAMETERS).offset; h.genericParametersSize = self._sec(SEC_GENERIC_PARAMETERS).size
        h.genericParameterConstraintsOffset = self._sec(SEC_GENERIC_PARAM_CONSTRAINTS).offset
        h.genericParameterConstraintsSize = self._sec(SEC_GENERIC_PARAM_CONSTRAINTS).size
        h.genericContainersOffset = sec_generic_containers.offset; h.genericContainersSize = sec_generic_containers.size
        h.nestedTypesOffset = self._sec(SEC_NESTED_TYPES).offset; h.nestedTypesSize = self._sec(SEC_NESTED_TYPES).size
        h.interfacesOffset = self._sec(SEC_INTERFACES).offset; h.interfacesSize = self._sec(SEC_INTERFACES).size
        h.vtableMethodsOffset = self._sec(SEC_VTABLE_METHODS).offset; h.vtableMethodsSize = self._sec(SEC_VTABLE_METHODS).size
        h.typeDefinitionsOffset = sec_typedefs.offset; h.typeDefinitionsSize = sec_typedefs.size
        h.imagesOffset = sec_images.offset; h.imagesSize = sec_images.size
        h.assembliesOffset = self._sec(SEC_ASSEMBLIES).offset; h.assembliesSize = self._sec(SEC_ASSEMBLIES).size
        h.fieldRefsOffset = self._sec(SEC_FIELD_REFS).offset; h.fieldRefsSize = self._sec(SEC_FIELD_REFS).size
        h.referencedAssembliesOffset = self._sec(SEC_REFERENCED_ASSEMBLIES).offset
        h.referencedAssembliesSize = self._sec(SEC_REFERENCED_ASSEMBLIES).size
        h.attributeDataOffset = self._sec(SEC_ATTRIBUTE_DATA).offset; h.attributeDataSize = self._sec(SEC_ATTRIBUTE_DATA).size
        h.attributeDataRangeOffset = self._sec(SEC_ATTRIBUTE_DATA_RANGES).offset
        h.attributeDataRangeSize = self._sec(SEC_ATTRIBUTE_DATA_RANGES).count * 8
        self.header = h

        self.imageDefs = self._read_images_v38(sec_images.count)
        self.typeDefs = self._read_type_defs_v38(sec_typedefs)
        self.methodDefs = self._read_method_defs_v38(sec_methods)
        self.parameterDefs = self._read_param_defs_v38(sec_params)
        self.fieldDefs = self._read_field_defs_v38(sec_fields)
        self._read_default_values_v38(self._sec(SEC_FIELD_DEFAULT_VALUES), self._sec(SEC_PARAM_DEFAULT_VALUES))
        self.propertyDefs = self._read_prop_defs_v38(self._sec(SEC_PROPERTIES))
        self.eventDefs = self._read_event_defs_v38(self._sec(SEC_EVENTS))
        self.genericContainers = self._read_generic_containers_v38(sec_generic_containers)
        self.genericParameters = self._read_generic_parameters_v38(self._sec(SEC_GENERIC_PARAMETERS))
        self.interfaceIndices = self._read_idx_arr_v38(self._sec(SEC_INTERFACES), self._typeIdxSize)
        self.nestedTypeIndices = self._read_idx_arr_v38(self._sec(SEC_NESTED_TYPES), self._typeDefIdxSize)

        self._read_custom_attributes()
        self.metadataUsagesCount = 0

    def _read_images_v38(self, count):
        out = []
        self.bin.seek(self._sec(SEC_IMAGES).offset)
        for _ in range(int(min(count, 0x7FFFFFFF))):
            b = self.bin
            d = ImageDef()
            d.nameIndex = b.read_u32()
            d.assemblyIndex = b.read_i32()
            d.typeStart = self._read_idx(self._typeDefIdxSize)
            d.typeCount = b.read_u32()
            self._read_idx(self._typeDefIdxSize)  # exportedTypeStart (tidak dipakai)
            b.read_u32()  # exportedTypeCount
            d.entryPointIndex = self._read_idx(4)
            d.token = b.read_u32()
            d.customAttributeStart = b.read_i32()
            d.customAttributeCount = b.read_u32()
            out.append(d)
        return out

    def _read_type_defs_v38(self, s):
        out = []
        b = self.bin
        b.seek(s.offset)
        end = s.offset + s.size
        while b.pos < end:
            t = TypeDef()
            t.customAttributeIndex = -1
            t.nameIndex = b.read_u32()
            t.namespaceIndex = b.read_u32()
            t.byvalTypeIndex = self._read_idx(self._typeIdxSize)
            t.declaringTypeIndex = self._read_idx(self._typeIdxSize)
            t.parentIndex = self._read_idx(self._typeIdxSize)
            t.genericContainerIndex = self._read_idx(self._genericContainerIdxSize)
            t.flags = b.read_u32()
            t.fieldStart = self._read_idx(4)
            t.methodStart = self._read_idx(4)
            t.eventStart = self._read_idx(4)
            t.propertyStart = self._read_idx(4)
            t.nestedTypesStart = self._read_idx(4)
            t.interfacesStart = self._read_idx(4)
            t.vtableStart = b.read_i32()
            t.interfaceOffsetsStart = self._read_idx(4)
            t.method_count = b.read_u16(); t.property_count = b.read_u16()
            t.field_count = b.read_u16(); t.event_count = b.read_u16()
            t.nested_type_count = b.read_u16(); t.vtable_count = b.read_u16()
            t.interfaces_count = b.read_u16(); t.interface_offsets_count = b.read_u16()
            t.bitfield = b.read_u32()
            t.token = b.read_u32()
            out.append(t)
        return out

    def _read_method_defs_v38(self, s):
        out = []
        b = self.bin
        b.seek(s.offset)
        end = s.offset + s.size
        while b.pos < end:
            m = MethodDef()
            m.customAttributeIndex = -1
            m.nameIndex = b.read_u32()
            m.declaringType = self._read_idx(self._typeDefIdxSize)
            m.returnType = self._read_idx(self._typeIdxSize)
            m.returnParameterToken = b.read_i32()
            m.parameterStart = self._read_idx(self._paramIdxSize)
            m.genericContainerIndex = self._read_idx(self._genericContainerIdxSize)
            m.token = b.read_u32()
            m.flags = b.read_u16(); m.iflags = b.read_u16()
            m.slot = b.read_u16(); m.parameterCount = b.read_u16()
            out.append(m)
        return out

    def _read_param_defs_v38(self, s):
        out = []
        b = self.bin
        b.seek(s.offset)
        end = s.offset + s.size
        while b.pos < end:
            p = ParamDef()
            p.customAttributeIndex = -1
            p.nameIndex = b.read_u32()
            p.token = b.read_u32()
            p.typeIndex = self._read_idx(self._typeIdxSize)
            out.append(p)
        return out

    def _read_field_defs_v38(self, s):
        out = []
        b = self.bin
        b.seek(s.offset)
        end = s.offset + s.size
        while b.pos < end:
            f = FieldDef()
            f.customAttributeIndex = -1
            f.nameIndex = b.read_u32()
            f.typeIndex = self._read_idx(self._typeIdxSize)
            f.token = b.read_u32()
            out.append(f)
        return out

    def _read_default_values_v38(self, f_sec, p_sec):
        b = self.bin
        b.seek(f_sec.offset)
        end = f_sec.offset + f_sec.size
        while b.pos < end:
            d = DefaultVal()
            d.index = self._read_idx(4)
            d.typeIndex = self._read_idx(self._typeIdxSize)
            d.dataIndex = self._read_idx(4)
            self.fieldDefaultValueDic.setdefault(d.index, d)
        b.seek(p_sec.offset)
        end = p_sec.offset + p_sec.size
        while b.pos < end:
            d = DefaultVal()
            d.index = self._read_idx(self._paramIdxSize)
            d.typeIndex = self._read_idx(self._typeIdxSize)
            d.dataIndex = self._read_idx(4)
            self.paramDefaultValueDic.setdefault(d.index, d)

    def _read_prop_defs_v38(self, s):
        out = []
        b = self.bin
        b.seek(s.offset)
        end = s.offset + s.size
        while b.pos < end:
            p = PropDef()
            p.customAttributeIndex = -1
            p.nameIndex = b.read_u32()
            p.get = self._read_idx(4)
            p.set = self._read_idx(4)
            p.attrs = b.read_u32()
            p.token = b.read_u32()
            out.append(p)
        return out

    def _read_event_defs_v38(self, s):
        out = []
        b = self.bin
        b.seek(s.offset)
        end = s.offset + s.size
        while b.pos < end:
            e = EventDef()
            e.customAttributeIndex = -1
            e.nameIndex = b.read_u32()
            e.typeIndex = self._read_idx(self._typeIdxSize)
            e.add = self._read_idx(4)
            e.remove = self._read_idx(4)
            e.raise_ = self._read_idx(4)
            e.token = b.read_u32()
            out.append(e)
        return out

    def _read_generic_containers_v38(self, s):
        out = []
        b = self.bin
        b.seek(s.offset)
        end = s.offset + s.size
        while b.pos < end:
            g = GenericContainer()
            g.ownerIndex = b.read_i32()
            g.type_argc = b.read_i32()
            g.is_method = b.read_i32()
            g.genericParameterStart = b.read_i32()
            out.append(g)
        return out

    def _read_generic_parameters_v38(self, s):
        out = []
        b = self.bin
        b.seek(s.offset)
        end = s.offset + s.size
        while b.pos < end:
            g = GenericParameter()
            g.ownerIndex = self._read_idx(self._genericContainerIdxSize)
            g.nameIndex = b.read_u32()
            g.constraintsStart = b.read_u16()
            g.constraintsCount = b.read_u16()
            g.num = b.read_u16()
            g.flags = b.read_u16()
            out.append(g)
        return out
