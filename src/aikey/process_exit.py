"""Observe a direct child's exit without reaping its process-group anchor."""

import os
import select


def supported():
    return (all(hasattr(os, name) for name in ("waitid", "WNOWAIT", "WEXITED", "WNOHANG", "P_PID"))
            or all(hasattr(select, name) for name in ("kqueue", "kevent", "KQ_FILTER_PROC", "KQ_NOTE_EXIT")))


class ExitWatcher:
    def __init__(self, pid):
        self.pid, self.queue, self.done = pid, None, False
        if not hasattr(os, "waitid"):
            self.queue = select.kqueue()
            try:
                event = select.kevent(pid, filter=select.KQ_FILTER_PROC,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT, fflags=select.KQ_NOTE_EXIT)
                self.queue.control([event], 0, 0)
            except BaseException:
                self.queue.close()
                raise

    def exited(self):
        if self.queue is None:
            return os.waitid(os.P_PID, self.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        if not self.done:
            events = self.queue.control(None, 1, 0)
            for event in events:
                if event.flags & select.KQ_EV_ERROR:
                    raise OSError("Cannot observe maintenance child exit")
                if event.fflags & select.KQ_NOTE_EXIT:
                    self.done = True
        return self.done

    def close(self):
        if self.queue is not None:
            self.queue.close()
