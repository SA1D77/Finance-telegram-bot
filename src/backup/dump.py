"""pg_dump into a gzip file and rotation of old copies. The password goes through PGPASSWORD, never argv."""
import gzip
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

PREFIX = 'finance-'
SUFFIX = '.sql.gz'
TIMEOUT_SECONDS = 600


@dataclass
class DbParams:
    host: str
    port: str
    user: str
    password: Optional[str]
    database: str


def dump_command(params: DbParams, pg_dump: str = 'pg_dump') -> List[str]:
    # --clean --if-exists: restoring over an existing database replaces its tables.
    return [pg_dump, '--host', params.host, '--port', str(params.port), '--username', params.user,
            '--dbname', params.database, '--format=plain', '--clean', '--if-exists', '--no-owner',
            '--no-privileges', '--encoding=UTF8']


def dump_env(params: DbParams) -> Dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith('PG')}
    if params.password:
        env['PGPASSWORD'] = params.password
    return env


def backup_name(now: datetime) -> str:
    return f"{PREFIX}{now.strftime('%Y-%m-%d_%H%M%S')}{SUFFIX}"


def create_backup(params: DbParams, directory: str, now: datetime, pg_dump: str = 'pg_dump') -> Path:
    """Writes directory/finance-<time>.sql.gz; a failed dump leaves no partial file behind."""
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / backup_name(now)
    partial = target.with_suffix('.part')
    process = subprocess.Popen(dump_command(params, pg_dump), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dump_env(params))
    try:
        with gzip.open(partial, 'wb') as out:
            shutil.copyfileobj(process.stdout, out)
        _, stderr = process.communicate(timeout=TIMEOUT_SECONDS)
        if process.returncode != 0:
            raise RuntimeError(f'pg_dump failed ({process.returncode}): {stderr.decode(errors="replace").strip()[:300]}')
        partial.replace(target)
        return target
    except BaseException:
        process.kill()
        partial.unlink(missing_ok=True)
        raise


def rotate(directory: str, keep: int) -> List[Path]:
    """Deletes all but the newest `keep` backups; returns the deleted paths."""
    files = sorted(Path(directory).glob(f'{PREFIX}*{SUFFIX}'), key=lambda path: path.name, reverse=True)
    removed = files[max(keep, 1):]
    for path in removed:
        path.unlink(missing_ok=True)
    return removed
