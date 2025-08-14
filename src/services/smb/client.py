import uuid
import logging
from typing import List, Dict, Any, cast
from pathlib import Path
from datetime import datetime
from smbprotocol.open import (
    Open,
    CreateOptions,
    CreateDisposition,
    ShareAccess,
    ImpersonationLevel,
    FileAttributes,
)
from smbprotocol.tree import TreeConnect
from smbprotocol.session import Session
from smbprotocol.file_info import FileInformationClass
from smbprotocol.connection import Connection
from smbprotocol.exceptions import (
    SMBAuthenticationError,
    SMBConnectionClosed,
    SMBException,
    LogonFailure,
    SMBResponseException,
)

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s | %(filename)s:%(lineno)s \t [%(levelname)s] %(message)s",
)


def _get_tree_and_path(session_info: dict, remote_path: str) -> tuple[TreeConnect, str]:
    server = session_info["server"]
    share = session_info["share"]
    username = session_info["username"]
    password = session_info["password"]

    conn = Connection(uuid.uuid4(), server, 445)
    conn.connect()
    session = Session(conn, username=username, password=password)
    session.connect()
    tree = TreeConnect(session, rf"\\{server}\{share}")
    tree.connect()

    rel_path = remote_path.strip("\\/")
    return tree, rel_path


def download_file(session_info: dict, remote_path: str, local_path: str) -> None:
    """
    Download a file from an SMB share to a local path.

    session_info: dict with keys server, share, username, password
    remote_path: path on the share (can include subdirectories)
    local_path: local filesystem destination
    """
    tree, rel_path = _get_tree_and_path(session_info, remote_path)

    file = Open(tree, rel_path)
    try:
        # 0x120089 = FILE_READ_DATA | FILE_READ_ATTRIBUTES | FILE_READ_EA | READ_CONTROL | SYNCHRONIZE
        file.create(
            desired_access=0x120089,
            share_access=ShareAccess.FILE_SHARE_READ,
            create_options=CreateOptions.FILE_NON_DIRECTORY_FILE,
            create_disposition=CreateDisposition.FILE_OPEN,
            impersonation_level=ImpersonationLevel.Impersonation,
            file_attributes=FileAttributes.FILE_ATTRIBUTE_NORMAL,
        )

        local_path_obj = Path(local_path)
        local_path_obj.parent.mkdir(parents=True, exist_ok=True)

        chunk_size = 1024 * 1024  # 1 MiB
        offset = 0

        with open(local_path_obj, "wb") as f_out:
            while True:
                try:
                    # Cast to bytes to satisfy static type checkers
                    # smbprotocol returns bytes when wait=True
                    data = cast(bytes, file.read(offset, chunk_size, wait=True))
                except SMBResponseException as e:
                    # Some servers return STATUS_END_OF_FILE as an error instead of empty payload
                    # treat as normal EOF
                    if "STATUS_END_OF_FILE" in str(e) or "0xc0000011" in str(e).lower():
                        break
                    raise
                if not data:
                    # Normal EOF condition
                    break
                f_out.write(data)
                offset += len(data)

    except SMBResponseException as e:
        logger.error(f"Failed to download {remote_path}: {e}")
        raise
    finally:
        try:
            file.close()
        except Exception:
            pass


def upload_file(session_info: dict, local_path: str) -> None:
    tree, filename = _get_tree_and_path(session_info, Path(local_path).name)

    with open(local_path, "rb") as f_in:
        data = f_in.read()

    file = Open(
        tree,
        filename,
    )
    file.create(
        create_options=CreateOptions.FILE_NON_DIRECTORY_FILE,
        create_disposition=CreateDisposition.FILE_OVERWRITE_IF,
        desired_access=0x12019F,  # GENERIC_WRITE | FILE_WRITE_DATA | FILE_APPEND_DATA etc
        share_access=ShareAccess.FILE_SHARE_READ,
        impersonation_level=ImpersonationLevel.Impersonation,
        file_attributes=FileAttributes.FILE_ATTRIBUTE_DIRECTORY,
    )
    try:
        file.write(data, 0)
    finally:
        file.close()


def connect_to_smb_share(server: str, share: str, username: str, password: str) -> Open:
    try:
        conn = Connection(uuid.uuid4(), server, 445)
        conn.connect()

        session = Session(conn, username, password)
        session.connect()

        tree = TreeConnect(session, rf"\\{server}\{share}")
        tree.connect()

        root = Open(tree, "")
        root.create(
            desired_access=0x00000001,
            share_access=ShareAccess.FILE_SHARE_READ,
            create_disposition=CreateDisposition.FILE_OPEN,
            create_options=CreateOptions.FILE_DIRECTORY_FILE,
            impersonation_level=ImpersonationLevel.Impersonation,
            file_attributes=FileAttributes.FILE_ATTRIBUTE_DIRECTORY,
        )

        return root

    except (
        LogonFailure,
        SMBAuthenticationError,
        SMBConnectionClosed,
        SMBException,
    ) as e:
        logger.error(f"SMB connection/authentication failed: {e}")
        raise

    except Exception as e:
        logger.error(f"Unexpected error during SMB connection: {e}")
        raise


def list_files_in_directory(root: Open) -> List[Dict[str, str]]:
    media: List[Dict[str, str]] = []
    try:
        for entry in root.query_directory(
            "*", FileInformationClass.FILE_DIRECTORY_INFORMATION
        ):
            try:
                entry_any = cast(Any, entry)
                try:
                    # Preferred access pattern
                    name_bytes = entry_any.fields["file_name"].value  # type: ignore[attr-defined]
                except Exception:
                    # Fallback for alternative structure access
                    name_bytes = entry_any["file_name"].value  # type: ignore[index]
                if isinstance(name_bytes, (bytes, bytearray)):
                    try:
                        name = name_bytes.decode("utf-16le", errors="strict")
                    except UnicodeDecodeError:
                        # Skip undecodable entries
                        logger.warning(
                            "Failed to strictly decode file name; skipping entry"
                        )
                        continue
                else:
                    name = str(name_bytes)

                # Filter out dot entries, BOM-only, empty, or names containing control chars
                if (
                    name in (".", "..")
                    or name == "\ufeff"  # stray BOM
                    or not name.strip()
                    or any(ord(c) < 32 for c in name)
                ):
                    continue
                # Attempt to extract size and directory attribute; fall back silently
                size_val = 0
                is_dir = False
                try:
                    if hasattr(entry_any, "fields"):
                        fields = entry_any.fields  # type: ignore[attr-defined]
                        eof_field = fields.get("end_of_file")
                        if eof_field is not None and hasattr(eof_field, "value"):
                            try:
                                size_val = int(getattr(eof_field, "value"))
                            except Exception:
                                size_val = 0
                        attr_field = fields.get("file_attributes")
                        if attr_field is not None and hasattr(attr_field, "value"):
                            try:
                                attrs = int(getattr(attr_field, "value"))
                                is_dir = bool(attrs & 0x10)  # FILE_ATTRIBUTE_DIRECTORY
                            except Exception:
                                is_dir = False
                        # FILE_DIRECTORY_INFORMATION often includes FILE_LAST_WRITE_TIME
                        # Available as 'last_write_time' or 'change_time' depending on lib
                        modified_val = None
                        for key in ("last_write_time", "change_time", "creation_time"):
                            fld = fields.get(key)
                            if fld is not None and hasattr(fld, "value"):
                                try:
                                    modified_val = getattr(fld, "value")
                                    break
                                except Exception:
                                    modified_val = None
                except Exception:
                    pass

                # Convert modified_val to ISO-like string if present (best-effort)
                mod_str: str | None = None
                try:
                    if modified_val is not None:
                        # many libs return 100-ns intervals since Jan 1, 1601 (Windows FILETIME)
                        # Detect large integers and convert to Unix epoch if needed.
                        val = int(modified_val)
                        # Heuristic: FILETIME ticks are very large numbers
                        if val > 10_000_000_000_000:
                            # Convert FILETIME (100-ns since 1601-01-01) to Unix seconds
                            unix_seconds = (val - 116444736000000000) / 10_000_000
                            dt = datetime.fromtimestamp(unix_seconds)
                        else:
                            dt = datetime.fromtimestamp(val)
                        mod_str = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    mod_str = None

                media.append(
                    {
                        "name": name,
                        "path": name,
                        "size": str(size_val),  # Keep as string for compatibility
                        "is_dir": "true" if is_dir else "false",
                        "modified": mod_str or "",
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to decode file name: {e}")
                continue

    except SMBResponseException as e:
        logger.error(f"Failed to query directory: {e}")

    return media
