from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os, json, threading, time, string, random

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, 'data')
CODES_FILE = os.path.join(DATA_DIR, 'codes.json')
LOG_FILE = os.path.join(DATA_DIR, 'logs.json')
LOCK = threading.Lock()

app = FastAPI(title='Access Codes API')
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the frontend static files located in ./static
static_path = os.path.join(BASE_DIR, 'static')
if not os.path.isdir(static_path):
    os.makedirs(static_path, exist_ok=True)
app.mount('/static', StaticFiles(directory=static_path), name='static')

# Ensure data dir and files exist
os.makedirs(DATA_DIR, exist_ok=True)
for f,init in ((CODES_FILE, '[]'), (LOG_FILE, '[]')):
    if not os.path.exists(f):
        with open(f, 'w', encoding='utf-8') as fh:
            fh.write(init)


def read_codes():
    with LOCK:
        with open(CODES_FILE, 'r', encoding='utf-8') as fh:
            try:
                return json.load(fh)
            except Exception:
                return []


def write_codes(data):
    with LOCK:
        with open(CODES_FILE, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)


def append_log(entry):
    with LOCK:
        arr = []
        try:
            with open(LOG_FILE, 'r', encoding='utf-8') as fh:
                arr = json.load(fh)
        except Exception:
            arr = []
        arr.append(entry)
        with open(LOG_FILE, 'w', encoding='utf-8') as fh:
            json.dump(arr, fh, ensure_ascii=False, indent=2)


def gen_code(length=6):
    chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return ''.join(random.choice(chars) for _ in range(length))


@app.get('/')
async def root():
    # Serve the access-codes page from the static folder
    p = os.path.join(static_path, 'access-codes.html')
    if os.path.exists(p):
        return FileResponse(p, media_type='text/html')
    return JSONResponse({'ok': True, 'msg': 'Access Codes API. Place your frontend in /static/access-codes.html'}, status_code=200)


@app.get('/api/ping')
async def ping():
    return {'ok': True}


@app.get('/api/codes')
async def get_codes():
    codes = read_codes()
    return {'codes': codes}


class CreateCode(BaseModel):
    label: str = ''
    type: str = 'multi'  # 'single' or 'multi'
    uses: int = 1


@app.post('/api/codes')
async def create_code(payload: CreateCode, request: Request):
    codes = read_codes()
    code = gen_code(6)
    item = {
        'code': code,
        'label': payload.label,
        'type': payload.type,
        'created': int(time.time()),
    }
    if payload.type == 'single':
        item['used'] = False
    else:
        item['usesRemaining'] = payload.uses
    codes.insert(0, item)
    write_codes(codes)
    append_log({'action': 'create', 'code': code, 'by': request.client.host, 'time': int(time.time())})
    return {'ok': True, 'code': item}


class ConsumeReq(BaseModel):
    code: str


@app.post('/api/consume')
async def consume_code(payload: ConsumeReq, request: Request):
    codes = read_codes()
    idx = next((i for i,c in enumerate(codes) if c['code'].upper() == payload.code.upper()), None)
    if idx is None:
        raise HTTPException(404, 'code not found')
    item = codes[idx]
    if item.get('type') == 'single':
        if item.get('used'):
            raise HTTPException(400, 'single use code already used')
        item['used'] = True
    else:
        if 'usesRemaining' in item:
            if item['usesRemaining'] <= 0:
                raise HTTPException(400, 'no remaining uses')
            item['usesRemaining'] -= 1
    codes[idx] = item
    write_codes(codes)
    append_log({'action': 'consume', 'code': item['code'], 'by': request.client.host, 'time': int(time.time())})
    return {'ok': True, 'code': item}


@app.delete('/api/codes/{code}')
async def delete_code(code: str, request: Request):
    codes = read_codes()
    idx = next((i for i,c in enumerate(codes) if c['code'].upper() == code.upper()), None)
    if idx is None:
        raise HTTPException(404, 'code not found')
    removed = codes.pop(idx)
    write_codes(codes)
    append_log({'action': 'delete', 'code': removed['code'], 'by': request.client.host, 'time': int(time.time())})
    return {'ok': True}
