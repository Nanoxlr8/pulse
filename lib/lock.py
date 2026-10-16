"""Single-writer guard for PULSE.

SQLite WAL allows one writer at a time. Two `pulse index` / `pulse content`
runs at once (say a manual run while the scheduled task is going) make one of
them die with "database is locked" partway through, losing that pass's work.

Read commands (find / grep / stat / doctor) never take this lock.
"""
import os
import sys
import time
import json


class Busy(Exception):
    pass


class WriteLock:
    def __init__(self, pulse_dir, wait=0):
        self.path = os.path.join(pulse_dir, "writer.lock")
        self.wait = wait
        self.fd = None

    def _stale(self):
        """True if the lock is held by a pid that is no longer running."""
        try:
            with open(self.path, encoding="utf-8") as f:
                info = json.load(f)
        except Exception:
            return True
        pid = info.get("pid")
        if not pid:
            return True
        try:
            import psutil
            if not psutil.pid_exists(pid):
                return True
            # Same pid reused by an unrelated process?
            p = psutil.Process(pid)
            if p.create_time() > info.get("started", 0) + 2:
                return True
            return False
        except ImportError:
            return False
        except Exception:
            return True

    def acquire(self):
        deadline = time.time() + self.wait
        while True:
            try:
                self.fd = os.open(self.path,
                                  os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(self.fd, json.dumps({
                    "pid": os.getpid(),
                    "started": time.time(),
                    "argv": " ".join(sys.argv[1:]),
                }).encode("utf-8"))
                return self
            except FileExistsError:
                if self._stale():
                    try:
                        os.unlink(self.path)
                        continue
                    except OSError:
                        pass
                if time.time() >= deadline:
                    holder = ""
                    try:
                        with open(self.path, encoding="utf-8") as f:
                            holder = json.load(f).get("argv", "")
                    except Exception:
                        pass
                    raise Busy(
                        "another PULSE write is already running"
                        + (" (pulse %s)" % holder if holder else "")
                        + " - wait for it, or use --wait N to queue")
                time.sleep(1.0)

    def release(self):
        try:
            if self.fd is not None:
                os.close(self.fd)
        except OSError:
            pass
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False
