from dataclasses import dataclass
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]

@dataclass(frozen=True)
class Settings:
    data: Path
    api_key: str
    model_id: str = 'black-forest-labs/FLUX.2-klein-4B'
    revision: str | None = None
    max_active_jobs: int = 16
    max_job_seconds: float = 300
    memory_fraction: float = .65
    hourly_eur: str = ''

    @classmethod
    def from_env(cls):
        from dotenv import load_dotenv
        load_dotenv(ROOT / '.env', override=False)
        data = Path(os.environ.get('DATA_DIR', str(ROOT / 'data'))).expanduser().resolve()
        key = os.environ.get('IMAGE_API_KEY', '')
        if len(key) < 32:
            raise RuntimeError('IMAGE_API_KEY must contain at least 32 characters. Run scripts/install.sh first.')
        memory = float(os.environ.get('GPU_MEMORY_FRACTION', '.65'))
        deadline = float(os.environ.get('MAX_JOB_SECONDS', '300'))
        active = int(os.environ.get('MAX_ACTIVE_JOBS', '16'))
        if not 0 < memory <= .9 or not 1 <= deadline <= 3600 or not 1 <= active <= 100:
            raise RuntimeError('Invalid memory, timeout or queue configuration.')
        hourly = os.environ.get('GPU_HOURLY_EUR', '')
        if hourly:
            from decimal import Decimal
            rate = Decimal(hourly)
            if not rate.is_finite() or rate <= 0:
                raise RuntimeError('GPU_HOURLY_EUR must be a positive finite number.')
        return cls(data=data, api_key=key, model_id=os.environ.get('MODEL_ID', cls.model_id),
                   revision=os.environ.get('MODEL_REVISION') or None, max_active_jobs=active,
                   max_job_seconds=deadline, memory_fraction=memory, hourly_eur=hourly)
