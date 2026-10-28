from contextlib import asynccontextmanager
from decimal import Decimal, ROUND_CEILING
import hmac
from uuid import UUID
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from .images import decode_reference
from .models import JobRequest
from .settings import Settings
from .store import Store, QueueFull, RequestConflict
from .worker import Worker

class RequestSizeLimit:
    def __init__(self, app, limit=8 * 1024 * 1024):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST':
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            part = message.get('body', b'')
            size += len(part)
            if size > self.limit:
                return await JSONResponse({'detail': 'Request exceeds 8 MB.'}, status_code=413)(scope, receive, send)
            chunks.append(part)
            if not message.get('more_body', False):
                break
        delivered = False
        async def buffered_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': b''.join(chunks), 'more_body': False}
            return await receive()
        return await self.app(scope, buffered_receive, send)

def create_app(settings=None, start_worker=True):
    settings = settings or Settings.from_env()
    store = Store(settings.data, settings.max_active_jobs)
    worker = Worker(settings, store)
    @asynccontextmanager
    async def lifespan(_app):
        if start_worker:
            worker.start()
        else:
            worker.ready = True
        yield
        if start_worker:
            worker.stop()
    app = FastAPI(title='Inspirator GPU API', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RequestSizeLimit)
    app.state.store, app.state.worker = store, worker
    bearer = HTTPBearer(auto_error=False)

    def authorize(token: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if token is None or not hmac.compare_digest(token.credentials.encode(), settings.api_key.encode()):
            raise HTTPException(401, 'Valid API bearer token required.', headers={'WWW-Authenticate': 'Bearer'})

    def response(job):
        job = {**job, 'model': settings.model_id}
        job['server_runtime_seconds'] = round(job.pop('runtime_ms') / 1000, 3)
        job['usage_complete'] = job['state'] in {'succeeded', 'failed', 'timed_out'}
        if settings.hourly_eur and job['usage_complete']:
            job['estimated_cost_eur_micros'] = int((Decimal(str(job['server_runtime_seconds'])) * Decimal(settings.hourly_eur) * 1_000_000 / 3600).to_integral_value(rounding=ROUND_CEILING))
        else:
            job['estimated_cost_eur_micros'] = None
        for image in job['images']:
            image['path'] = f"/v1/jobs/{job['id']}/images/{image['id']}"
        return job

    @app.get('/livez')
    def live():
        return {'status': 'alive'}

    @app.get('/health')
    def health():
        return JSONResponse({'status': 'ready' if worker.ready else 'error' if worker.error else 'loading'}, status_code=200 if worker.ready else 503)

    @app.post('/v1/jobs', status_code=202, dependencies=[Depends(authorize)])
    def submit(payload: JobRequest):
        if not worker.ready:
            raise HTTPException(503, 'The image model is still loading or unavailable.')
        try:
            for value in payload.references:
                decode_reference(value)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if payload.max_run_seconds > settings.max_job_seconds:
            raise HTTPException(422, 'Requested runtime exceeds the server limit.')
        try:
            job_id, created = store.submit(payload.model_dump(mode='json'))
        except QueueFull as exc:
            raise HTTPException(429, str(exc), headers={'Retry-After': '30'}) from exc
        except RequestConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        worker.wakeup.set()
        return {**response(store.get(job_id)), 'created': created}

    @app.get('/v1/jobs/{job_id}', dependencies=[Depends(authorize)])
    def status(job_id: UUID):
        job = store.get(str(job_id))
        if not job:
            raise HTTPException(404, 'Job not found.')
        return response(job)

    @app.get('/v1/jobs/{job_id}/images/{image_id}', dependencies=[Depends(authorize)])
    def image(job_id: UUID, image_id: UUID):
        path = store.image_file(str(job_id), str(image_id))
        if not path:
            raise HTTPException(404, 'Image not found.')
        return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})

    return app
