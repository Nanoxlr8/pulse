"""Fast filesystem crawler for PULSE."""
import os
import time

SEP = os.sep  # "\\" on Windows

# Directory names skipped anywhere in the tree (lowercased, exact match).
SKIP_NAMES = {
    "node_modules", "__pycache__", ".git", ".svn", ".hg", ".venv", "venv",
    "$recycle.bin", "system volume information", ".cache", ".next", ".nuxt",
    "site-packages", ".gradle", ".m2", "locallow",
    "temp", "tmp", "cache", "cache2", "caches", "codecache", "gpucache",
    "crashpad", "cachestorage", "service worker", "blob_storage",
    ".idea", ".vscode-server", "pnpm-store", ".nvm", "dawncache",
    "shadercache", "gpucache", "indexeddb", "local storage",
}

# Path fragments skipped (lowercased substring match against the full path).
# Written with os.sep so this stays correct if ever run off Windows.
_FRAGS = [
    ["appdata", "local", "packages"],
    ["appdata", "local", "microsoft", "edge"],
    ["appdata", "local", "google", "chrome", "user data"],
    ["appdata", "local", "programs", "python"],
    ["appdata", "local", "claude"],
    ["appdata", "local", "anthropic"],
    ["appdata", "roaming", "code"],
    ["windows"],
    ["program files"],
    ["program files (x86)"],
    ["programdata"],
    [".cursor"],
]
SKIP_FRAGMENTS = tuple(SEP + SEP.join(f) + SEP for f in _FRAGS)

SKIP_EXT = {
    ".tmp", ".log", ".lock", ".pyc", ".pyo", ".obj", ".o", ".pdb", ".ilk",
    ".crdownload", ".part", ".etl", ".dmp",
}


def _skip_dir(full_lower, name_lower):
    if name_lower in SKIP_NAMES:
        return True
    for frag in SKIP_FRAGMENTS:
        if frag in full_lower:
            return True
    return False


def crawl(root_path, root_name, on_batch, batch_size=4000,
          max_depth=40, stop_after=None, progress=None):
    """Iteratively walk root_path, calling on_batch(rows) as batches fill.

    row = (path, name, ext, dirpath, size, mtime, root_name)
    Returns (n_files, n_dirs, elapsed_seconds, truncated).
    """
    t0 = time.time()
    batch = []
    n_files = n_dirs = 0
    truncated = False
    stack = [(root_path, 0)]

    while stack:
        if stop_after and (time.time() - t0) > stop_after:
            truncated = True
            break
        cur, depth = stack.pop()
        if depth > max_depth:
            continue
        try:
            it = os.scandir(cur)
        except (PermissionError, OSError):
            continue
        n_dirs += 1
        if progress and n_dirs % 2000 == 0:
            progress(n_files, n_dirs, time.time() - t0)
        try:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if not _skip_dir(e.path.lower(), e.name.lower()):
                            stack.append((e.path, depth + 1))
                        continue
                    if not e.is_file(follow_symlinks=False):
                        continue
                    name = e.name
                    ext = os.path.splitext(name)[1].lower()
                    if ext in SKIP_EXT:
                        continue
                    st = e.stat(follow_symlinks=False)
                    batch.append((e.path, name, ext, cur,
                                  st.st_size, st.st_mtime, root_name))
                    n_files += 1
                    if len(batch) >= batch_size:
                        on_batch(batch)
                        batch = []
                except (PermissionError, OSError, ValueError):
                    continue
        finally:
            try:
                it.close()
            except Exception:
                pass

    if batch:
        on_batch(batch)
    return n_files, n_dirs, time.time() - t0, truncated
