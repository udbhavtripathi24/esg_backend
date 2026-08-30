"""Run: python -m app.workers.runner"""
import logging
from app.workers import run_forever


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


if __name__ == "__main__":  # pragma: no cover
    run_forever()
