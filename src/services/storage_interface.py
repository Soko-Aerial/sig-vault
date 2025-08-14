from __future__ import annotations

from typing import Any, Dict, List, Protocol, Tuple, Union, cast


StorageEntry = Dict[str, Union[str, int, float]]


class StorageBackend(Protocol):
    def connect(self, session_info: Dict[str, str]) -> Any: ...
    def list_files(self, handle: Any) -> List[StorageEntry]: ...
    def download(
        self, session_info: Dict[str, str], remote: str, local: str
    ) -> None: ...
    def upload(self, session_info: Dict[str, str], local_path: str) -> None: ...


def _smb_backend() -> StorageBackend:
    # Lazy imports to keep optional deps optional
    from src.services.smb.client import (
        connect_to_smb_share as smb_connect,
        list_files_in_directory as smb_list,
        download_file as smb_download,
        upload_file as smb_upload,
    )

    class SMBBackend:
        def connect(self, session_info: Dict[str, str]) -> Any:
            # server, share, username, password expected
            return smb_connect(
                server=session_info.get("server", ""),
                share=session_info.get("share", ""),
                username=session_info.get("username", ""),
                password=session_info.get("password", ""),
            )

        def list_files(self, handle: Any) -> List[StorageEntry]:
            # Underlying SMB returns List[Dict[str, str]], which is runtime-compatible
            # with our StorageEntry but not covariant for typing. Cast for clarity.
            return cast(List[StorageEntry], smb_list(handle))

        def download(
            self, session_info: Dict[str, str], remote: str, local: str
        ) -> None:
            smb_download(session_info, remote, local)

        def upload(self, session_info: Dict[str, str], local_path: str) -> None:
            smb_upload(session_info, local_path)

    return SMBBackend()


def _dav_backend() -> StorageBackend:
    # Lazy import to avoid mandatory dependency when unused
    from src.services.dav.client import OwnCloudWebDAVClient, WebDAVAuthError

    class DAVBackend:
        def _client(self, session_info: Dict[str, str]) -> OwnCloudWebDAVClient:
            base = session_info.get("server", "")  # Using 'server' to store base URL
            user = session_info.get("username", "")
            pwd = session_info.get("password", "")
            return OwnCloudWebDAVClient(base_url=base, username=user, password=pwd)

        def connect(
            self, session_info: Dict[str, str]
        ) -> Tuple[OwnCloudWebDAVClient, str]:
            # Returns (client, current_path)
            client = self._client(session_info)
            # Start at root
            return client, ""

        def list_files(self, handle: Any) -> List[StorageEntry]:
            client, path = handle if isinstance(handle, tuple) else (handle, "")
            try:
                entries = client.list(path)
            except WebDAVAuthError as e:
                raise RuntimeError(str(e)) from e
            result: List[StorageEntry] = []
            for e in entries:
                name = str(e.get("name", ""))
                size = e.get("size")
                is_dir = bool(e.get("is_dir"))
                out: StorageEntry = {
                    "name": name,
                    "path": name,
                    "size": str(size if size is not None else 0),
                    "is_dir": "true" if is_dir else "false",
                }
                # Include modified when available to populate UI "Date modified"
                mod = e.get("modified")
                if mod is not None:
                    # Keep raw form; UI will format various types/strings
                    out["modified"] = (
                        str(mod) if not isinstance(mod, (int, float)) else mod
                    )
                result.append(out)
            return result

        def download(
            self, session_info: Dict[str, str], remote: str, local: str
        ) -> None:
            client = self._client(session_info)
            client.download(remote, local)

        def upload(self, session_info: Dict[str, str], local_path: str) -> None:
            client = self._client(session_info)
            # Upload to root with same filename
            import os

            remote = os.path.basename(local_path)
            client.upload(local_path, remote)

    return DAVBackend()


def get_backend(session_info: Dict[str, str]) -> StorageBackend:
    storage = (
        session_info.get("storage") or session_info.get("backend") or "smb"
    ).lower()
    if storage == "cloud":
        return _dav_backend()
    return _smb_backend()


def connect(session_info: Dict[str, str]) -> Any:
    return get_backend(session_info).connect(session_info)


def list_files(handle: Any) -> List[StorageEntry]:
    # handle must be created by connect()
    # Attempt to infer backend from handle when possible; else default to SMB adapter
    # but simpler: the handle is backend-specific; we can't infer reliably, so expect caller to hold on to backend or re-connect
    # For this project, FileExplorer uses wrappers that maintain session_info.
    raise NotImplementedError("Use BackendAwareExplorer wrappers for list_files")


def download_file(session_info: Dict[str, str], remote: str, local: str) -> None:
    return get_backend(session_info).download(session_info, remote, local)


def upload_file(session_info: Dict[str, str], local_path: str) -> None:
    return get_backend(session_info).upload(session_info, local_path)
