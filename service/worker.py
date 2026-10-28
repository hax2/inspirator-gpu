import logging
import os
import secrets
import threading
import time
import uuid
from .engine import FluxEngine, GenerationDeadline
from .images import decode_reference

log = logging.getLogger('inspirator.worker')

class Worker:
    def __init__(self, settings, store, engine=None):
        self.settings, self.store = settings, store
        self.engine = engine or FluxEngine(settings)
        self.stopping = threading.Event()
        self.wakeup = threading.Event()
        self.ready = False
        self.error = False
        self.thread = None
        self.lock_file = None

    def start(self):
        import fcntl
        self.lock_file = (self.settings.data / 'worker.lock').open('a+')
        try:
            fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            raise RuntimeError('Another Inspirator GPU worker already owns this data directory. Use one Uvicorn worker.')
        self.store.recover()
        self.thread = threading.Thread(target=self.run, name='gpu-worker', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        self.wakeup.set()
        if self.thread:
            self.thread.join(timeout=5)
        # Keep the process lock until process exit if inference is still active.
        if self.lock_file and not (self.thread and self.thread.is_alive()):
            self.lock_file.close()

    def run(self):
        try:
            self.engine.load()
            self.ready = True
            log.info('Image model is ready.')
        except Exception:
            self.error = True
            log.exception('Model startup failed.')
            return
        while not self.stopping.is_set():
            try:
                job = self.store.claim()
                if job:
                    self.process(job)
                else:
                    self.wakeup.wait(.5)
                    self.wakeup.clear()
            except Exception:
                self.ready, self.error = False, True
                log.exception('Worker storage loop failed; refusing new jobs.')
                return

    def process(self, job):
        started = time.monotonic()
        payload = job['payload']
        deadline = started + min(payload['max_run_seconds'], self.settings.max_job_seconds)
        destination = self.settings.data / 'outputs' / job['id']
        destination.mkdir(parents=True, exist_ok=True)
        try:
            refs = [decode_reference(value) for value in payload['references']]
            base_seed = payload['seed'] if payload['seed'] is not None else secrets.randbits(32)
            for index, prompt in enumerate(payload['prompts']):
                if self.stopping.is_set() or time.monotonic() >= deadline:
                    raise GenerationDeadline()
                seed = (base_seed + index) % 4_294_967_296
                image_started = time.monotonic()
                image = self.engine.generate(prompt, refs, payload['width'], payload['height'], seed, deadline, self.stopping)
                runtime = max(1, round((time.monotonic() - image_started) * 1000))
                image_id = str(uuid.uuid4())
                temporary = destination / (image_id + '.tmp')
                image.convert('RGB').save(temporary, format='JPEG', quality=90)
                os.replace(temporary, destination / (image_id + '.jpg'))
                self.store.append_image(job['id'], index, image_id, seed, runtime)
            self.store.finish(job['id'], runtime_ms=max(1, round((time.monotonic() - started) * 1000)))
        except GenerationDeadline:
            self.store.finish(job['id'], 'interrupted' if self.stopping.is_set() else 'timed_out',
                              'Generation stopped; completed images are retained. Verify usage before retrying.',
                              max(1, round((time.monotonic() - started) * 1000)))
        except Exception:
            log.exception('Generation failed for job %s', job['id'])
            self.store.finish(job['id'], 'failed', 'Generation failed. Check server logs; completed images are retained.',
                              max(1, round((time.monotonic() - started) * 1000)))
