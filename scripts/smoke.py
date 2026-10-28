"""Submit one image and download it locally, without printing the API key."""
import json
import sys
import time
import urllib.request
import urllib.error
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from service.settings import Settings
settings = Settings.from_env()
base = sys.argv[1].rstrip('/') if len(sys.argv) > 1 else 'http://127.0.0.1:8010'
if not (base.startswith('https://') or base.startswith('http://127.0.0.1:')):
    raise SystemExit('Use HTTPS remotely or localhost for this authenticated test.')
headers = {'Authorization': 'Bearer ' + settings.api_key}
payload = {'request_id': str(uuid.uuid4()), 'prompts': ['A sculptural terracotta table lamp, soft studio lighting, product design photograph'], 'seed': 42}
def call(path, data=None):
    request = urllib.request.Request(base + path, data=json.dumps(data).encode() if data else None,
                                     headers={**headers, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)
job = call('/v1/jobs', payload)
print('Submitted job ' + job['id'])
for _ in range(180):
    job = call('/v1/jobs/' + job['id'])
    print(f"{job['state']}: {job['completed']}/{job['total']}")
    if job['state'] == 'succeeded':
        request = urllib.request.Request(base + job['images'][0]['path'], headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            (settings.data / 'smoke.jpg').write_bytes(response.read())
        print('Saved data/smoke.jpg. Runtime: ' + str(job['server_runtime_seconds']) + ' seconds.')
        break
    if job['state'] in {'failed', 'timed_out', 'interrupted'}:
        raise SystemExit(job['error'])
    time.sleep(2)
else:
    raise SystemExit('Job is still queued. Poll its existing ID; do not blindly resubmit.')
