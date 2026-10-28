import base64
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import tempfile
from pathlib import Path
import time
import unittest
import uuid
from fastapi.testclient import TestClient
from PIL import Image
from service.api import create_app
from service.models import JobRequest
from service.settings import Settings
from service.store import Store, QueueFull, RequestConflict
from service.worker import Worker

KEY = 'test-api-key-' + 'a' * 40

def payload(**kwargs):
    return {'request_id': str(uuid.uuid4()), 'prompts': ['A sculptural ceramic lamp'], 'references': [],
            'width': 768, 'height': 768, 'seed': 42, 'max_run_seconds': 300, **kwargs}

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.settings = Settings(data=Path(self.directory.name), api_key=KEY, max_active_jobs=1, hourly_eur='2')
        self.app = create_app(self.settings, start_worker=False)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.headers = {'Authorization': 'Bearer ' + KEY}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def post(self, body, **kwargs):
        return self.client.post('/v1/jobs', json=body, headers=self.headers, **kwargs)

    def test_health_and_readiness(self):
        self.assertEqual(self.client.get('/health').status_code, 200)
        self.app.state.worker.ready = False
        self.assertEqual(self.client.get('/health').status_code, 503)
        self.assertEqual(self.client.get('/livez').status_code, 200)
        self.assertEqual(self.post(payload()).status_code, 503)

    def test_authentication_required_for_jobs_status_and_images(self):
        self.assertEqual(self.client.post('/v1/jobs', json=payload()).status_code, 401)
        self.assertEqual(self.client.get('/v1/jobs/' + str(uuid.uuid4())).status_code, 401)
        self.assertEqual(self.client.get('/v1/jobs/' + str(uuid.uuid4()) + '/images/' + str(uuid.uuid4())).status_code, 401)
        self.assertEqual(self.client.post('/v1/jobs', json=payload(), headers={'Authorization': 'Bearer bad'}).status_code, 401)

    def test_request_id_is_idempotent_even_when_queue_is_full(self):
        request = payload()
        first = self.post(request)
        second = self.post(request)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json()['id'], second.json()['id'])
        self.assertFalse(second.json()['created'])
        self.assertEqual(self.post(payload()).status_code, 429)
        request['prompts'] = ['Another different lamp']
        self.assertEqual(self.post(request).status_code, 409)

    def test_limits_and_remote_reference_urls_are_rejected(self):
        for invalid in [payload(prompts=['  ']), payload(prompts=['A lamp'] * 51), payload(width=4000),
                        payload(references=['https://127.0.0.1/private']), payload(references=['data:image/svg+xml;base64,YQ==']),
                        payload(max_run_seconds=301), {**payload(), 'steps': 1000}]:
            self.assertEqual(self.post(invalid).status_code, 422)

    def test_valid_reference_is_accepted(self):
        data = BytesIO()
        Image.new('RGB', (32, 32), 'red').save(data, format='PNG')
        reference = 'data:image/png;base64,' + base64.b64encode(data.getvalue()).decode()
        response = self.post(payload(references=[reference]))
        self.assertEqual(response.status_code, 202)

    def test_body_size_is_bounded(self):
        response = self.client.post('/v1/jobs', content=b'x' * (8 * 1024 * 1024 + 1), headers=self.headers)
        self.assertEqual(response.status_code, 413)

    def test_private_image_download_and_no_cross_job_lookup(self):
        job_id = self.post(payload()).json()['id']
        image_id = str(uuid.uuid4())
        folder = self.settings.data / 'outputs' / job_id
        folder.mkdir(parents=True)
        Image.new('RGB', (32, 32)).save(folder / (image_id + '.jpg'))
        self.app.state.store.append_image(job_id, 0, image_id, 1, 50)
        url = f'/v1/jobs/{job_id}/images/{image_id}'
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get(f'/v1/jobs/{uuid.uuid4()}/images/{image_id}', headers=self.headers).status_code, 404)

    def test_accounting_is_estimated_and_unknown_jobs_do_not_report_zero_cost(self):
        job_id = self.post(payload()).json()['id']
        self.assertIsNone(self.client.get('/v1/jobs/' + job_id, headers=self.headers).json()['estimated_cost_eur_micros'])
        self.app.state.store.finish(job_id, runtime_ms=1800)
        result = self.client.get('/v1/jobs/' + job_id, headers=self.headers).json()
        self.assertTrue(result['usage_complete'])
        self.assertEqual(result['estimated_cost_eur_micros'], 1000)
        self.app.state.store.finish(job_id, 'interrupted')
        self.assertIsNone(self.client.get('/v1/jobs/' + job_id, headers=self.headers).json()['estimated_cost_eur_micros'])

class StoreAndWorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.store = Store(self.root, max_active=50)

    def tearDown(self):
        self.directory.cleanup()

    def test_duplicate_concurrent_submissions_have_one_job(self):
        request = payload()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.submit(request), range(20)))
        self.assertEqual(len({r[0] for r in results}), 1)
        self.assertEqual(sum(r[1] for r in results), 1)

    def test_job_claim_is_atomic(self):
        for _ in range(12):
            self.store.submit(payload())
        with ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda _: self.store.claim(), range(20)))
        ids = [c['id'] for c in claims if c]
        self.assertEqual(len(ids), 12)
        self.assertEqual(len(set(ids)), 12)

    def test_restart_marks_inflight_work_interrupted_without_requeue(self):
        job_id, _ = self.store.submit(payload())
        self.store.claim()
        self.store.append_image(job_id, 0, str(uuid.uuid4()), 1, 50)
        self.store.recover()
        self.assertEqual(self.store.get(job_id)['state'], 'interrupted')
        self.assertEqual(self.store.get(job_id)['completed'], 1)
        self.assertIsNone(self.store.claim())

    def test_gpu_worker_persists_each_image(self):
        class Engine:
            def generate(self, *args):
                return Image.new('RGB', (32, 32), 'blue')
        job_id, _ = self.store.submit(payload(prompts=['A lamp', 'Another lamp']))
        Worker(Settings(data=self.root, api_key=KEY), self.store, Engine()).process(self.store.claim())
        job = self.store.get(job_id)
        self.assertEqual(job['state'], 'succeeded')
        self.assertEqual(job['completed'], 2)
        self.assertEqual([i['seed'] for i in job['images']], [42, 43])
        self.assertTrue(self.store.image_file(job_id, job['images'][0]['id']).is_file())

    def test_partial_failure_preserves_finished_images(self):
        class Engine:
            calls = 0
            def generate(self, *args):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError('fake GPU failure')
                return Image.new('RGB', (32, 32))
        job_id, _ = self.store.submit(payload(prompts=['A lamp', 'Another lamp']))
        with self.assertLogs('inspirator.worker', level='ERROR'):
            Worker(Settings(data=self.root, api_key=KEY), self.store, Engine()).process(self.store.claim())
        self.assertEqual(self.store.get(job_id)['state'], 'failed')
        self.assertEqual(self.store.get(job_id)['completed'], 1)

    def test_runtime_limit_stops_between_images(self):
        class Engine:
            def generate(self, *args):
                time.sleep(.08)
                return Image.new('RGB', (32, 32))
        job_id, _ = self.store.submit(payload(prompts=['A lamp', 'Another lamp'], max_run_seconds=.05))
        Worker(Settings(data=self.root, api_key=KEY), self.store, Engine()).process(self.store.claim())
        self.assertEqual(self.store.get(job_id)['state'], 'timed_out')
        self.assertLessEqual(self.store.get(job_id)['completed'], 1)

if __name__ == '__main__':
    unittest.main()
