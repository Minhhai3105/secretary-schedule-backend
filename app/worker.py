import argparse
import logging
import time

from sqlalchemy.orm import Session

from app.db.session import engine
from app.services.reminders import run_reminder_cycle

logger = logging.getLogger(__name__)


def run_once() -> int:
    with Session(engine) as db:
        processed = run_reminder_cycle(db)
        return len(processed)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Secretary Schedule email reminder worker."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single reminder scan and delivery cycle, then exit.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Polling interval in seconds when running continuously (default: 30).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.once:
        count = run_once()
        logger.info("Processed %d email deliveries.", count)
        return

    interval = max(5, args.interval)
    logger.info("Starting email reminder worker (interval=%ds)...", interval)
    while True:
        try:
            count = run_once()
            if count:
                logger.info("Processed %d email deliveries.", count)
        except Exception:
            logger.exception("Reminder worker cycle failed")
        time.sleep(interval)


if __name__ == "__main__":
    main()
