"""Background worker: runs queued jobs, and installs the sample pack on first start.

Several jobs run at once in threads (``IR_WORKER_THREADS``), so one book's
pictures and narration are prepared side by side and one visitor's long job
does not hold up another's upload. PDFs are still parsed in child processes.

Run from the repository root:  .venv/bin/python -m imagine_reader_server.worker
"""

import logging
import signal
import time
from concurrent.futures import Future, ThreadPoolExecutor

from .config import Settings
from .db import make_engine
from .jobs import claim_next, recover_interrupted, run_claimed
from .sample import resume_samples
from .sample_pack import install_in_background

HOUSEKEEPING_EVERY_SECONDS = 60
IDLE_SLEEP_SECONDS = 1

log = logging.getLogger("imagine_reader_server.worker")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()
    engine = make_engine(settings)
    stopping = False

    def stop(signum, _frame):
        nonlocal stopping
        stopping = True
        log.info("stopping after the current job (signal %s)", signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    recover_interrupted(engine, settings)
    log.info("worker started; data in %s", settings.data_dir)
    last_housekeeping = 0.0
    running: set[Future] = set()
    with ThreadPoolExecutor(max(1, settings.worker_threads), thread_name_prefix="job") as pool:
        while not stopping:
            if time.monotonic() - last_housekeeping >= HOUSEKEEPING_EVERY_SECONDS:
                if settings.samples:
                    install_in_background(engine, settings)  # once: the samples' voice and pictures, ready-made
                    if settings.prepare_samples:
                        resume_samples(engine, settings)
                last_housekeeping = time.monotonic()
            running = {job for job in running if not job.done()}
            claimed = claim_next(engine) if len(running) < settings.worker_threads else None
            if claimed is None:
                time.sleep(IDLE_SLEEP_SECONDS if not running else 0.2)
                continue
            running.add(pool.submit(run_claimed, engine, settings, claimed))


if __name__ == "__main__":
    main()
