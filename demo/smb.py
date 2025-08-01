import pprint
import uuid#
from getpass import getpass
from typing import List, Dict

from smbprotocol.open import (
    Open,
    CreateOptions,
    CreateDisposition,
    ShareAccess,
    ImpersonationLevel,
    FileAttributes,
)
from smbprotocol.session import Session
from smbprotocol.tree import TreeConnect
from smbprotocol.connection import Connection
from smbprotocol.file_info import FileInformationClass


SERVER = "10.223.6.248"
SHARE = "mock_nas"
FOLDER = ""
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def decode_utf16le(hex_bytes: bytes) -> str:
    """
    Decodes a UTF-16LE encoded byte string to a standard Python string.

    Args:
        hex_bytes (bytes): A byte string encoded in UTF-16LE.

    Returns:
        str: The decoded string.
    """
    return hex_bytes.decode("utf-16le")


def is_media_file(filename: str) -> bool:
    """
    Checks if a given filename has a supported image or video file extension.

    Args:
        filename (str): The name of the file.

    Returns:
        bool: True if the file is a supported media file, False otherwise.
    """
    return any(filename.lower().endswith(ext) for ext in IMAGE_EXTS | VIDEO_EXTS)


def list_media_files_recursive(directory: Open, base_path: str = "") -> List[Dict]:
    """
    Recursively lists all media files (images and videos) in a given directory
    on an SMB share.

    Args:
        directory (Open): The directory to scan, represented as an SMB Open object.
        base_path (str): The path relative to the root of the SMB share, used for recursion.

    Returns:
        List[Dict]: A list of dictionaries containing metadata for each media file found:
            - name (str): The filename.
            - path (str): The full relative path of the file.
            - created (int): Windows FILETIME creation timestamp.
            - last_access (int): Last access FILETIME.
            - last_modified (int): Last modified FILETIME.
            - size_bytes (int): File size in bytes.
            - is_video (bool): True if the file is a video.
            - is_image (bool): True if the file is an image.
    """
    media_files = []

    for entry in directory.query_directory(
        "*", FileInformationClass.FILE_DIRECTORY_INFORMATION
    ):
        fields = entry.fields
        raw_name = fields["file_name"].value
        name = decode_utf16le(raw_name)
        full_path = f"{base_path}\\{name}" if base_path else name
        is_dir = bool(
            fields["file_attributes"].value & FileAttributes.FILE_ATTRIBUTE_DIRECTORY
        )

        if name in [".", ".."]:
            continue

        if is_dir:
            subdir = Open(directory.tree, f"{directory.file_name}\\{name}")
            subdir.create(
                desired_access=0x00000001,
                share_access=ShareAccess.FILE_SHARE_READ,
                create_disposition=CreateDisposition.FILE_OPEN,
                create_options=CreateOptions.FILE_DIRECTORY_FILE,
                impersonation_level=ImpersonationLevel.Impersonation,
                file_attributes=FileAttributes.FILE_ATTRIBUTE_DIRECTORY,
            )
            media_files.extend(list_media_files_recursive(subdir, full_path))
            subdir.close()
        elif is_media_file(name):
            media_files.append(
                {
                    "name": name,
                    "path": full_path,
                    "created": fields["creation_time"].value,
                    "last_access": fields["last_access_time"].value,
                    "last_modified": fields["last_write_time"].value,
                    "size_bytes": fields["end_of_file"].value,
                    "is_video": name.lower().endswith(tuple(VIDEO_EXTS)),
                    "is_image": name.lower().endswith(tuple(IMAGE_EXTS)),
                }
            )

    return media_files


if __name__ == "__main__":
    username = input("Enter SMB username: ")
    password = getpass("Enter SMB password: ")

    conn = Connection(guid=uuid.uuid4(), server_name=SERVER, port=445)
    conn.connect()

    session = Session(conn, username=username, password=password)
    session.connect()

    tree = TreeConnect(session, rf"\\{SERVER}\{SHARE}")
    tree.connect()

    root_dir = Open(tree, FOLDER)
    root_dir.create(
        desired_access=0x00000001,
        share_access=ShareAccess.FILE_SHARE_READ,
        create_disposition=CreateDisposition.FILE_OPEN,
        create_options=CreateOptions.FILE_DIRECTORY_FILE,
        impersonation_level=ImpersonationLevel.Impersonation,
        file_attributes=FileAttributes.FILE_ATTRIBUTE_DIRECTORY,
    )

    media = list_media_files_recursive(root_dir)
    root_dir.close()

    for m in media:
        pprint.pprint(m)
