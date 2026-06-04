"""Manejo del estado de los jobs de procesamiento.

Jobs en memoria (dict) por simplicidad en MVP. Si se necesita persistencia
o concurrencia robusta después, migrar a una store externa.
"""
import threading
import uuid

# job_id -> dict
_jobs: dict[str, dict] = {}
_lock = threading.Lock()

VALID_STATUSES = {"pending", "processing", "done", "error"}


def create_job(input_path: str, output_path: str) -> str:
    """Crea un job nuevo y retorna su job_id."""
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "pending",
            "progress": 0,
            "input_path": input_path,
            "output_path": output_path,
            "error": None,
        }
    return job_id


def get_job(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def update_job(job_id: str, **fields) -> None:
    """Actualiza campos arbitrarios del job (status, progress, error, ...)."""
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        if "status" in fields and fields["status"] not in VALID_STATUSES:
            raise ValueError(f"status inválido: {fields['status']}")
        job.update(fields)


def set_progress(job_id: str, progress: int) -> None:
    update_job(job_id, progress=max(0, min(100, int(progress))))
