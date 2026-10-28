import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

class QueueFull(Exception):
    pass

class RequestConflict(Exception):
    pass

class Store:
    def __init__(self, root: Path, max_active=16):
        self.root = root
        self.max_active = max_active
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'jobs.sqlite3'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                    payload TEXT NOT NULL, state TEXT NOT NULL, total INTEGER NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
                    runtime_ms INTEGER NOT NULL DEFAULT 0, error TEXT, created REAL NOT NULL, updated REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS images (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, position INTEGER NOT NULL, seed INTEGER NOT NULL,
                    runtime_ms INTEGER NOT NULL, filename TEXT NOT NULL, UNIQUE(job_id, position)
                );
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def recover(self):
        # Unknown in-flight costs must be reconciled, never silently regenerated.
        with self.connect() as db:
            db.execute("UPDATE jobs SET state='interrupted', error='Worker restarted during generation; completed images are retained.', updated=? WHERE state='running'", (time.time(),))

    def submit(self, payload):
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'))
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        now, job_id = time.time(), str(uuid.uuid4())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT id, fingerprint FROM jobs WHERE request_id=?', (payload['request_id'],)).fetchone()
            if existing:
                if existing['fingerprint'] != fingerprint:
                    raise RequestConflict('Request ID was already used with different inputs.')
                return existing['id'], False
            active = db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued', 'running')").fetchone()[0]
            if active >= self.max_active:
                raise QueueFull('GPU queue is full. Try again later.')
            db.execute('INSERT INTO jobs (id, request_id, fingerprint, payload, state, total, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                       (job_id, payload['request_id'], fingerprint, encoded, 'queued', len(payload['prompts']), now, now))
        return job_id, True

    def claim(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET state='running', updated=? WHERE id=?", (time.time(), row['id']))
            return {**dict(row), 'payload': json.loads(row['payload'])}

    def append_image(self, job_id, position, image_id, seed, runtime_ms):
        filename = f'{job_id}/{image_id}.jpg'
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO images VALUES (?, ?, ?, ?, ?, ?)', (image_id, job_id, position, seed, runtime_ms, filename))
            db.execute('UPDATE jobs SET completed=completed+1, runtime_ms=runtime_ms+?, updated=? WHERE id=?', (runtime_ms, time.time(), job_id))

    def finish(self, job_id, state='succeeded', error=None, runtime_ms=None):
        with self.connect() as db:
            if runtime_ms is None:
                db.execute('UPDATE jobs SET state=?, error=?, updated=? WHERE id=?', (state, error, time.time(), job_id))
            else:
                db.execute('UPDATE jobs SET state=?, error=?, runtime_ms=MAX(runtime_ms, ?), updated=? WHERE id=?', (state, error, runtime_ms, time.time(), job_id))

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT id, request_id, state, total, completed, runtime_ms, error, created, updated FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                return None
            images = db.execute('SELECT id, position, seed, runtime_ms FROM images WHERE job_id=? ORDER BY position', (job_id,)).fetchall()
            return {**dict(row), 'images': [dict(image) for image in images]}

    def image_file(self, job_id, image_id):
        with self.connect() as db:
            row = db.execute('SELECT filename FROM images WHERE job_id=? AND id=?', (job_id, image_id)).fetchone()
            if not row:
                return None
            path = (self.root / 'outputs' / row['filename']).resolve()
            if not path.is_relative_to((self.root / 'outputs').resolve()):
                return None
            return path if path.is_file() else None
