# Global caption admission ledger

`aikey.caption_budget.CaptionBudget` is the durable accounting part of [issue #12](https://github.com/Olli0103/unifi-ai-key-emulator/issues/12). It is **not wired into live job admission yet** and does not enable all-camera processing. The adopted Mac container still uses its explicit, single-use camera permit.

The ledger reserves at most 12 new paid caption attempts in any rolling hour across one shared state directory. The future caller must reserve before fetching media or contacting a model. Failed calls, timeouts and uncertain provider outcomes continue to count. Multiple processes use one file lock; restarting the service cannot refund a reservation. Repeating an identical job within the 24-hour replay window returns the previous reservation, while a changed fingerprint or camera ID fails closed. The worker's own result journal remains responsible for returning completed results.

The file is replaced atomically and synced. A missing file starts empty; a corrupt file, unsafe path, write failure or backward wall-clock step blocks new work. The journal keeps 24 hours of reservation identities and has a hard size cap. The ledger uses wall-clock time, so a forward clock jump is not distinguishable from elapsed time after a shutdown. The runtime integration should obtain a trusted time source or at least detect large forward jumps before automatic processing is enabled.

Before enabling all-camera mode, issue #12 still needs a fresh camera registry at admission, fair scheduling, a bounded result-journal lifecycle, a single transaction boundary between queue admission and budget reservation, and native persistence checks on another camera family. A caption rate limit alone does not make unsupported recognition or legacy ingress work.
