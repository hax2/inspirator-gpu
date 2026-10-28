import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
from service.settings import Settings
Settings.from_env()
import uvicorn

if __name__ == '__main__':
    uvicorn.run('service.api:create_app', factory=True, host=os.environ.get('HOST', '127.0.0.1'),
                port=int(os.environ.get('PORT', '8010')), workers=1, log_level='info')
