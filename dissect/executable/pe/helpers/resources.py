from __future__ import annotations

from collections import OrderedDict
from io import BytesIO
from typing import TYPE_CHECKING, Iterator

from dissect.executable.exception import ResourceException
from dissect.executable.pe.c_pe import c_pe

if TYPE_CHECKING:
    from dissect.cstruct.cstruct import cstruct
    from dissect.cstruct.types.enum import EnumInstance

    from dissect.executable.pe.helpers.sections import PESection
    from dissect.executable.pe.pe import PE


class ResourceManager:
    """Base class to perform actions regarding the resources within the PE file.

    Args:
        pe: A `PE` object.
        section: The section object that contains the resource table.
    """

    def __init__(self, pe: PE, section: PESection):
        self.pe = pe
        self.section = section
        self.resources = OrderedDict()
        self.raw_resources = []

        self.parse_rsrc()

    def parse_rsrc(self):
        """Parse the resource directory entry of the PE file."""

        rsrc_data = BytesIO(
            self.pe.read_image_directory(index=c_pe.IMAGE_DIRECTORY_ENTRY_RESOURCE)
        )
        self.resources = self._read_resource(
            rc_type="_root", data=rsrc_data, offset=0, level=1
        )

    def _read_entries(self, data: bytes, directory: cstruct) -> list:
        """Read the entries within the resource directory.

        Args:
            data: The data of the resource directory.
            directory: The resource directory entry.

        Returns:
            A list containing the entries of the resource directory.
        """

        entries = []
        for _ in range(0, directory.NumberOfNamedEntries + directory.NumberOfIdEntries):
            entry_offset = data.tell()
            entry = c_pe.IMAGE_RESOURCE_DIRECTORY_ENTRY(data)
            self.raw_resources.append(
                {"offset": entry_offset, "entry": entry, "data_offset": entry_offset}
            )
            entries.append(entry)
        return entries

    def _handle_data_entry(self, data: bytes, entry: cstruct, rc_type: str) -> Resource:
        """Handle the data entry of a resource. This is the actual data associated with the directory entry.

        Args:
            data: The data of the resource.
            entry: The resource directory entry.

        Returns:
            The resource that was given by name as a `Resource` object.
        """

        data.seek(entry.OffsetToDirectory)
        data_entry = c_pe.IMAGE_RESOURCE_DATA_ENTRY(data)
        self.pe.seek(data_entry.OffsetToData)
        data = self.pe.read(data_entry.Size)
        raw_offset = data_entry.OffsetToData - self.section.virtual_address
        rsrc = Resource(
            pe=self.pe,
            section=self.section,
            name=entry.Name,
            entry_offset=entry.OffsetToData,
            data_entry=data_entry,
            rc_type=rc_type,
        )
        self.raw_resources.append(
            {
                "offset": entry.OffsetToDirectory,
                "entry": data_entry,
                "data": data,
                "data_offset": raw_offset,
                "resource": rsrc,
            }
        )
        return rsrc

    def _read_resource(
        self, data: bytes, offset: int, rc_type: str, level: int = 1
    ) -> dict:
        """Recursively read the resources within the PE file.

        Each resource is added to the dictionary that is available to the user, as well as a list of
        raw resources that are used to update the section data and size when a resource has been modified.

        Args:
            data: The data of the resource.
            offset: The offset of the resource.
            rc_type: The type of the resource.
            level: The depth level of the resource, this dictates the resource type.

        Returns:
            A dictionary containing the resources that were found.
        """

        resource = OrderedDict()

        data.seek(offset)
        directory = c_pe.IMAGE_RESOURCE_DIRECTORY(data)
        self.raw_resources.append(
            {"offset": offset, "entry": directory, "data_offset": offset}
        )

        entries = self._read_entries(data, directory)

        for entry in entries:
            if level == 1:
                rc_type = c_pe.ResourceID(entry.Id).name
            else:
                if entry.NameIsString:
                    data.seek(entry.NameOffset)
                    name_len = c_pe.uint16(data)
                    rc_type = c_pe.wchar[name_len](data)
                else:
                    rc_type = str(entry.Id)

            if entry.DataIsDirectory:
                resource[rc_type] = self._read_resource(
                    data=data,
                    offset=entry.OffsetToDirectory,
                    rc_type=rc_type,
                    level=level + 1,
                )
            else:
                resource[rc_type] = self._handle_data_entry(
                    data=data, entry=entry, rc_type=rc_type
                )

        return resource

    def get_resource(self, name: str) -> Resource:
        """Retrieve the resource by name.

        Args:
            name: The name of the resource to retrieve.

        Returns:
            The resource that was given by name as a `Resource` object.
        """

        try:
            return self.resources[name]
        except KeyError:
            raise ResourceException(f"Resource {name} not found!")

    def get_resource_type(self, rsrc_id: str | EnumInstance):
        """Yields a generator containing all of the nodes within the resources that contain the requested ID.

        The ID can be either given by name or its value.

        Args:
            rsrc_id: The resource ID to find, this can be a cstruct `EnumInstance` or `str`.

        Yields:
            All of the nodes that contain the requested type.
        """

        if rsrc_id not in self.resources:
            raise ResourceException(f"Resource with ID {rsrc_id} not found in PE!")

        for resource in self.parse_resources(resources=self.resources[rsrc_id]):
            yield resource

    def parse_resources(self, resources: dict) -> Iterator[Resource]:
        """Parse the resources within the PE file.

        Args:
            resources: A `dict` containing the different resources that were found.

        Yields:
            All of the resources within the PE file.
        """

        for _, resource in resources.items():
            if type(resource) is not OrderedDict:
                yield resource
            else:
                yield from self.parse_resources(resources=resource)

    def show_resource_tree(self, resources: dict, indent: int = 0):
        """Print the resources within the PE as a tree.

        Args:
            resources: A `dict` containing the different resources that were found.
            indent: The amount of indentation for each child resource.
        """

        for name, resource in resources.items():
            if type(resource) is not OrderedDict:
                print(f"{' ' * indent} - name: {name} ID: {resource.rsrc_id}")
            else:
                print(f"{' ' * indent} + name: {name}")
                self.show_resource_tree(resources=resource, indent=indent + 1)

    def show_resource_info(self, resources: dict):
        """Print basic information about the resource as well as the header.

        Args:
            resources: A `dict` containing the different resources that were found.
        """

        for name, resource in resources.items():
            if type(resource) is not OrderedDict:
                print(
                    f"* resource: {name} offset=0x{resource.offset:02x} size=0x{resource.size:02x} header: {resource.data[:64]}"  # noqa: E501
                )
            else:
                self.show_resource_info(resources=resource)

    def add_resource(self, name: str, data: bytes):
        # TODO
        raise NotImplementedError

    def delete_resource(self, name: str):
        # TODO
        raise NotImplementedError

    def update_section(self, update_offset: int):
        """Function to dynamically update the section data and size when a resource has been modified.

        Args:
            update_offset: The offset of the resource that was modified.
        """

        new_size = 0
        section_data = b""

        for idx, resource in enumerate(
            self.parse_resources(resources=self.pe.resources)
        ):
            if idx == 0:
                # Use the offset of the first resource to account for the size of the directory header
                header_size = resource.offset - self.section.virtual_address
                section_data = self.section.data[:header_size]

            # Take note of the previous offset and size so we can update any of these values after changing the data
            # within the resource
            prev_offset = resource.offset
            prev_size = resource.size

            # Update the resource data
            section_data += resource.data

            new_size += resource.size + 1  # Account for the id field

            # Skip the resources that are below our own offset
            if update_offset >= resource.offset:
                continue

            offset = prev_offset + prev_size + 2
            resource.offset = offset

        # Add the header to the total size so we can check if we need to update the section size
        new_size += header_size

        # Update the section
        self.section.data = section_data


class Resource:
    """Base class representing a resource entry in the PE file.

    Args:
        pe: A `PE` object.
        section: The section object that contains the resource table.
        name: The name of the resource.
        entry_offset: The offset of the resource entry.
        data_entry: The data entry of the resource.
        rc_type: The type of the resource.
        data: The data of the resource if there was data provided by the user.
    """

    def __init__(
        self,
        pe: PE,
        section: PESection,
        name: str | int,
        entry_offset: int,
        data_entry: cstruct,
        rc_type: str,
        data: bytes = b"",
    ):
        self.pe = pe
        self.section = section
        self.name = name
        self.entry_offset = entry_offset
        self.entry = data_entry
        self.rc_type = rc_type
        self.offset = data_entry.OffsetToData
        self._size = data_entry.Size
        self.codepage = data_entry.CodePage
        self._data = self.read_data() if not data else data

    def read_data(self) -> bytes:
        """Read the data within the resource.

        Returns:
            The resource data.
        """

        return self.pe.virtual_read(address=self.offset, size=self._size)

    @property
    def size(self) -> int:
        """Function to return the size of the resource.
        This needs to be done dynamically in the case that the data is patched by the user.

        Returns:
            The size of the data within the resource.
        """

        return len(self.data)

    @size.setter
    def size(self, value: int) -> int:
        """Setter to set the size of the resource to the specified value.

        Args:
            value: The size of the resource.
        """

        self._size = value
        self.entry.Size = value

    @property
    def offset(self) -> int:
        """Return the offset of the resource."""
        return self.entry.OffsetToData

    @offset.setter
    def offset(self, value: int):
        """Setter to set the offset of the resource to the specified value.

        Args:
            value: The offset of the resource.
        """

        self.entry.OffsetToData = value

    @property
    def data(self) -> bytes:
        """Return the data within the resource."""
        return self._data

    @data.setter
    def data(self, value: bytes):
        """Setter to set the new data of the resource, but also dynamically update the offset of the resources within
        the same directory.

        This function currently also updates the section sizes and alignment. Ideally this would be moved to a more
        abstract function that
        can handle tasks like these in a more transparant manner.

        Args:
            value: The new data of the resource.
        """

        # Set the new data
        self._data = value

        if len(value) != self.entry.Size:
            self.size = len(value)

        section_data = BytesIO()

        prev_offset = 0
        prev_size = 0

        for rsrc_entry in sorted(
            self.pe.raw_resources, key=lambda rsrc: rsrc["data_offset"]
        ):
            entry_offset = rsrc_entry["offset"]
            entry = rsrc_entry["entry"]

            if entry._type.name == "IMAGE_RESOURCE_DATA_ENTRY":
                rsrc_obj = rsrc_entry["resource"]
                data_offset = rsrc_entry["data_offset"]

                # Normally the data is separated by a null byte, increment the new offset by 1
                new_data_offset = prev_offset + prev_size
                # if new_data_offset and (new_data_offset > data_offset or new_data_offset < data_offset):
                if new_data_offset and new_data_offset != data_offset:
                    data_offset = new_data_offset
                    rsrc_entry["data_offset"] = data_offset
                    rsrc_obj.offset = self.section.virtual_address + data_offset

                data = rsrc_obj.data

                # Write the resource entry data into the section
                section_data.seek(data_offset)
                section_data.write(data)

                # Take note of the offset and size so we can update any of these values after changing the data within
                # the resource
                prev_offset = data_offset
                prev_size = rsrc_obj.size

            # Write the resource entry into the section
            section_data.seek(entry_offset)
            section_data.write(entry.dumps())

        section_data.seek(0)
        data = section_data.read()

        # Update the section data and size
        self.section.data = data
        self.pe.optional_header.DataDirectory[
            c_pe.IMAGE_DIRECTORY_ENTRY_RESOURCE
        ].Size = len(data)

    def __str__(self) -> str:
        return str(self.name)

    def __repr__(self) -> str:
        return f"<ResourceEntry name={self.name} id={self.rc_type} offset=0x{self.offset:02x} size=0x{self.size:02x} codepage=0x{self.codepage:02x}>"  # noqa: E501