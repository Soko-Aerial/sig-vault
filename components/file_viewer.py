# Compatibility shim for tests that import `components.file_viewer`.
# Forward to the real implementation under `src.components.file_viewer`.
from components.file_tree_viewer import *  # noqa: F401,F403
