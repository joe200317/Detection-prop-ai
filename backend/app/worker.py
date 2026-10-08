import asyncio
import logging

from pymongo.errors import PyMongoError

from app.config import settings
from app.database import ensure_database
from app.services.jobs import claim_job, fail_job, run_job

logger = logging.getLogger(__name__)


async def job_worker(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            db = await ensure_database()
            job = await claim_job(db)
        except PyMongoError:
            job = None
            db = None
        if job is None or db is None:
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
            except TimeoutError:
                pass
            continue
        try:
            await run_job(db, job)
        except Exception as exc:
            logger.exception("Job %s failed", job.get("jobId"))
            try:
                await fail_job(db, job["jobId"], str(exc) or "Job failed")
            except PyMongoError:
                logger.exception("Could not record job failure for %s", job.get("jobId"))
