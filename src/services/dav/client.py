import os
from typing import List, Dict, Any, cast
from webdav3.client import Client
import concurrent.futures


class WebDAVError(Exception):
    """Base exception for WebDAV operations."""


class WebDAVAuthError(WebDAVError):
    """Authentication/authorization related errors (e.g., 401/403)."""


class WebDAVNotFoundError(WebDAVError):
    """Resource not found (e.g., 404)."""


class WebDAVConnectionError(WebDAVError):
    """Connectivity or unexpected server response."""


class OwnCloudWebDAVClient:
    """
    Thin wrapper around webdavclient3 for ownCloud/Nextcloud.
    Base must include username and trailing slash:
        https://HOST/remote.php/dav/files/<USERNAME>/
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        verify: bool = True,
        logger=None,
    ):
        # Ensure base_url matches ownCloud/Nextcloud DAV endpoint
        if not base_url.endswith("/"):
            base_url += "/"
        if "/remote.php/dav/files/" not in base_url:
            # Try to auto-correct for ownCloud
            base_url = base_url.rstrip("/") + f"/remote.php/dav/files/{username}/"
        self.base = base_url
        self.username = username
        self.password = password  # Store password for ownCloud OCS API
        self.logger = logger

        self.client = Client(
            {
                "webdav_hostname": self.base,
                "webdav_login": username,
                "webdav_password": password,
                "verify": verify,
            }
        )

    # -------- helpers --------
    def _ensure_dir(self, path: str) -> str:
        """Normalize WebDAV collection paths to end with /"""
        return path if (not path or path.endswith("/")) else path + "/"

    def _raise_mapped(self, action: str, exc: Exception) -> None:
        """Map library exceptions to our typed errors with friendly messages."""
        msg = str(exc) if exc else ""
        lower = msg.lower()
        if any(
            code in lower
            for code in [" 401", "code 401", "notauthenticated", "unauthorized"]
        ):
            friendly = (
                f"Authentication failed (401) while trying to {action}. "
                "Please verify your username, password, and server URL."
            )
            raise WebDAVAuthError(friendly) from exc
        if any(code in lower for code in [" 403", "code 403", "forbidden"]):
            friendly = f"Access forbidden (403) while trying to {action}. Check your permissions."
            raise WebDAVAuthError(friendly) from exc
        if any(code in lower for code in [" 404", "code 404", "not found"]):
            friendly = f"Resource not found (404) while trying to {action}."
            raise WebDAVNotFoundError(friendly) from exc
        # Fallback
        raise WebDAVConnectionError(f"WebDAV error during {action}: {msg}") from exc

    # -------- operations --------
    def list(self, remote_dir: str = "") -> List[Dict]:
        """List one level deep under remote_dir."""
        remote_dir = self._ensure_dir(remote_dir)
        try:
            entries = self.client.list(remote_dir)
        except Exception as e:
            if self.logger:
                self.logger.error(f"WebDAV list failed: {e}")
            else:
                print(f"WebDAV list failed: {e}")
            self._raise_mapped("list directory", e)
        # Build base results first (cheap)
        results: List[Dict[str, Any]] = []
        info_indices: Dict[str, int] = {}
        for name in entries:
            if name in ("", remote_dir.strip("/")):
                continue
            full = remote_dir + name if not name.startswith(remote_dir) else name
            is_dir = full.endswith("/")
            entry = {
                "name": name.rstrip("/"),
                "remote_path": full,
                "is_dir": is_dir,
                "size": None,
                "modified": None,
            }
            # Track all items for an info() pass to enrich metadata
            info_indices[full] = len(results)
            results.append(entry)

        # Optionally allow disabling directory metadata fetch via env var
        fetch_dirs_env = os.getenv("SIG_WEB_DAV_INFO_DIRECTORIES", "1").strip()
        include_dirs = fetch_dirs_env not in {"0", "false", "no"}

        # Concurrently fetch info() for entries to enrich size/modified
        if info_indices:
            max_workers_env = os.getenv("SIG_WEB_DAV_INFO_WORKERS")
            try:
                max_workers = int(max_workers_env) if max_workers_env else 8
            except Exception:
                max_workers = 8
            # Cap to avoid too many concurrent connections
            max_workers = max(1, min(16, max_workers))

            def _fetch(
                path: str,
            ) -> tuple[str, Dict[str, Any] | None, Exception | None]:
                try:
                    info = cast(Dict[str, Any], self.client.info(path))
                    return path, info, None
                except Exception as e:  # noqa: BLE001
                    return path, None, e

            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                # If directory metadata is disabled, filter to files only
                paths = [
                    p
                    for p, idx in info_indices.items()
                    if include_dirs or not results[idx]["is_dir"]
                ]
                futures = [ex.submit(_fetch, p) for p in paths]
                for fut in concurrent.futures.as_completed(futures):
                    path, info, err = fut.result()
                    idx = info_indices.get(path)
                    if idx is None:
                        continue
                    if err is not None:
                        if self.logger:
                            self.logger.warning(f"Could not get info for {path}: {err}")
                        continue
                    # Parse size and modified
                    size_val: Any = None
                    if not results[idx]["is_dir"]:
                        raw_size = info.get("size") if isinstance(info, dict) else None
                        if isinstance(raw_size, (int, str)):
                            try:
                                size_val = int(raw_size)
                            except (TypeError, ValueError):
                                size_val = None
                    modified = None
                    if isinstance(info, dict):
                        modified = (
                            info.get("modified")
                            or info.get("last_modified")
                            or info.get("mtime")
                            or info.get("updated_at")
                            or info.get("date")
                        )
                    # Only set size for files
                    if not results[idx]["is_dir"]:
                        results[idx]["size"] = size_val
                    results[idx]["modified"] = modified
        return results

    def get_owncloud_capabilities(self) -> Dict[str, Any]:
        """
        Query ownCloud server for capabilities (e.g., version, features).
        Returns a dict, or empty dict if not available.
        """
        import requests

        try:
            # Remove trailing slash for .well-known
            url = self.base.rstrip("/").split("/remote.php/dav/files/")[0]
            cap_url = url + "/ocs/v1.php/cloud/capabilities?format=json"
            resp = requests.get(cap_url, auth=(self.username, self.password))
            if resp.status_code == 200:
                return resp.json().get("ocs", {}).get("data", {})
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Could not fetch ownCloud capabilities: {e}")
        return {}

    def get_owncloud_quota(self) -> Dict[str, Any]:
        """
        Get user quota info from ownCloud (if supported).
        Returns a dict with quota info, or empty dict if not available.
        """
        import requests

        try:
            url = self.base.rstrip("/").split("/remote.php/dav/files/")[0]
            quota_url = url + f"/ocs/v1.php/cloud/users/{self.username}?format=json"
            resp = requests.get(quota_url, auth=(self.username, self.password))
            if resp.status_code == 200:
                return resp.json().get("ocs", {}).get("data", {}).get("quota", {})
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Could not fetch ownCloud quota: {e}")
        return {}

    def download(self, remote_path: str, local_path: str):
        """Download a single file."""
        os.makedirs(os.path.dirname(os.path.abspath(local_path)) or ".", exist_ok=True)
        self.client.download_sync(remote_path=remote_path, local_path=local_path)

    def upload(self, local_path: str, remote_path: str):
        """Upload a single file (PUT)."""
        # Create parent directories if needed
        parent = "/".join(remote_path.split("/")[:-1])
        if parent:
            self.makedirs(parent)
        self.client.upload_sync(remote_path=remote_path, local_path=local_path)

    def mkdir(self, remote_dir: str):
        """Create a single directory (MKCOL)."""
        remote_dir = self._ensure_dir(remote_dir)
        self.client.mkdir(remote_dir)

    def makedirs(self, remote_dir: str):
        """Create nested directories."""
        parts = [p for p in remote_dir.strip("/").split("/") if p]
        cur = ""
        for p in parts:
            cur = f"{cur}/{p}" if cur else p
            try:
                self.mkdir(cur)
            except Exception:
                pass  # exists

    def mirror_down(self, remote_dir: str, local_dir: str, *, overwrite: bool = True):
        """
        Recursively download a directory tree.
        NOTE: webdavclient3's download_sync already handles dir mirroring.
        """
        os.makedirs(local_dir, exist_ok=True)
        self.client.download_sync(
            remote_path=self._ensure_dir(remote_dir), local_path=local_dir
        )

    def delete(self, remote_path: str):
        self.client.clean(remote_path)
