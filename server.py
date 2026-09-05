#!/usr/bin/env python3
"""Novin shared production system - standard-library LAN server."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from pathlib import Path
from datetime import datetime, timedelta
import sqlite3, json, hashlib, secrets, mimetypes, threading, os, sys

BASE = Path(__file__).resolve().parent
STATIC = BASE / 'static'
DATA = Path(os.environ.get('NOVIN_DATA_DIR', str(BASE / 'data'))).expanduser().resolve()
DB_PATH = DATA / 'production.db'
HOST = os.environ.get('NOVIN_HOST', '0.0.0.0')
PORT = int(os.environ.get('PORT', os.environ.get('NOVIN_PORT', '8080')))
ADMIN_USER = os.environ.get('NOVIN_ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.environ.get('NOVIN_ADMIN_PASSWORD', 'admin123')
WRITE_LOCK = threading.RLock()
SESSIONS = {}
LOGIN_ATTEMPTS = {}
SESSION_LOCK = threading.Lock()


def connect():
    con = sqlite3.connect(DB_PATH, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    # DELETE mode is safer than WAL when the folder is accidentally run from a Windows network share.
    con.execute('PRAGMA journal_mode=DELETE')
    con.execute('PRAGMA synchronous=NORMAL')
    return con


def pass_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt), 200_000).hex()
    return f'{salt}${digest}'


def pass_ok(password, stored):
    try:
        salt, digest = stored.split('$', 1)
        return secrets.compare_digest(pass_hash(password, salt).split('$', 1)[1], digest)
    except Exception:
        return False


def init_db():
    DATA.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash TEXT NOT NULL,
          name TEXT NOT NULL,
          role TEXT NOT NULL CHECK(role IN ('manager','employee')),
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS products(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code TEXT NOT NULL UNIQUE COLLATE NOCASE,
          name TEXT NOT NULL,
          brand TEXT NOT NULL DEFAULT '',
          size TEXT NOT NULL DEFAULT '',
          carton_meterage REAL NOT NULL CHECK(carton_meterage >= 0),
          cartons_per_pallet REAL NOT NULL CHECK(cartons_per_pallet >= 0),
          description TEXT NOT NULL DEFAULT '',
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS entries(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          production_date TEXT NOT NULL,
          jalali_date TEXT NOT NULL,
          product_id INTEGER NOT NULL REFERENCES products(id),
          full_pallets REAL NOT NULL CHECK(full_pallets >= 0),
          broken_cartons REAL NOT NULL CHECK(broken_cartons >= 0),
          grade TEXT NOT NULL,
          total_cartons REAL NOT NULL,
          total_meterage REAL NOT NULL,
          user_id INTEGER REFERENCES users(id),
          user_name TEXT NOT NULL,
          notes TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_entries_date ON entries(production_date);
        CREATE INDEX IF NOT EXISTS idx_entries_product ON entries(product_id);
        ''')
        if con.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
            con.execute('INSERT INTO users(username,password_hash,name,role) VALUES(?,?,?,?)',
                        (ADMIN_USER, pass_hash(ADMIN_PASSWORD), 'مدیر سیستم', 'manager'))


def row_dict(row):
    return dict(row) if row is not None else None


def clean_sessions():
    now = datetime.now()
    with SESSION_LOCK:
        dead = [k for k, v in SESSIONS.items() if v['expires'] < now]
        for k in dead:
            SESSIONS.pop(k, None)


class Handler(BaseHTTPRequestHandler):
    server_version = 'NovinProduction/1.0'

    def log_message(self, fmt, *args):
        sys.stdout.write('[%s] %s\n' % (self.log_date_time_string(), fmt % args))

    def json_response(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        self.end_headers()
        self.wfile.write(raw)

    def error_json(self, message, status=400):
        self.json_response({'ok': False, 'error': message}, status)

    def read_json(self):
        length = int(self.headers.get('Content-Length', 0))
        if length > 10 * 1024 * 1024:
            raise ValueError('حجم درخواست بیش از حد مجاز است')
        raw = self.rfile.read(length)
        return json.loads(raw.decode('utf-8')) if raw else {}

    def auth(self, manager=False):
        clean_sessions()
        auth = self.headers.get('Authorization', '')
        token = auth[7:] if auth.startswith('Bearer ') else ''
        with SESSION_LOCK:
            sess = SESSIONS.get(token)
            if sess:
                sess['expires'] = datetime.now() + timedelta(hours=12)
        if not sess:
            self.error_json('نشست شما منقضی شده است؛ دوباره وارد شوید', 401)
            return None
        with connect() as con:
            user = con.execute('SELECT id,username,name,role,active FROM users WHERE id=?', (sess['user_id'],)).fetchone()
        if not user or not user['active']:
            self.error_json('کاربر غیرفعال است', 401)
            return None
        if manager and user['role'] != 'manager':
            self.error_json('این عملیات فقط برای مدیر مجاز است', 403)
            return None
        return dict(user)

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            q = parse_qs(parsed.query)
            if path == '/api/health':
                return self.json_response({'ok': True, 'server_time': datetime.now().isoformat(timespec='seconds')})
            if path == '/api/me':
                user = self.auth()
                if user: return self.json_response({'ok': True, 'user': user})
                return
            if path == '/api/products':
                if not self.auth(): return
                with connect() as con:
                    rows = [dict(r) for r in con.execute('SELECT * FROM products WHERE active=1 ORDER BY code')]
                return self.json_response({'ok': True, 'products': rows})
            if path == '/api/entries':
                if not self.auth(): return
                where, params = ['1=1'], []
                if q.get('date'):
                    where.append('e.production_date=?'); params.append(q['date'][0])
                if q.get('from'):
                    where.append('e.production_date>=?'); params.append(q['from'][0])
                if q.get('to'):
                    where.append('e.production_date<=?'); params.append(q['to'][0])
                sql = '''SELECT e.*,p.code,p.name product_name,p.brand,p.size,p.carton_meterage,p.cartons_per_pallet
                         FROM entries e JOIN products p ON p.id=e.product_id
                         WHERE %s ORDER BY e.production_date,e.id''' % ' AND '.join(where)
                with connect() as con:
                    rows = [dict(r) for r in con.execute(sql, params)]
                return self.json_response({'ok': True, 'entries': rows})
            if path == '/api/users':
                if not self.auth(manager=True): return
                with connect() as con:
                    rows = [dict(r) for r in con.execute('SELECT id,username,name,role,active,created_at FROM users ORDER BY id')]
                return self.json_response({'ok': True, 'users': rows})
            if path == '/api/backup':
                if not self.auth(manager=True): return
                with connect() as con:
                    data = {
                        'version': 1,
                        'exported_at': datetime.now().isoformat(timespec='seconds'),
                        'products': [dict(r) for r in con.execute('SELECT * FROM products ORDER BY id')],
                        'entries': [dict(r) for r in con.execute('SELECT * FROM entries ORDER BY id')],
                        'users': [dict(r) for r in con.execute('SELECT id,username,name,role,active,created_at FROM users ORDER BY id')]
                    }
                return self.json_response({'ok': True, 'backup': data})
            return self.serve_static(path)
        except Exception as exc:
            self.error_json('خطای داخلی سرور: ' + str(exc), 500)

    def client_ip(self):
        forwarded = self.headers.get('X-Forwarded-For', '')
        return forwarded.split(',')[0].strip() if forwarded else self.client_address[0]

    def do_POST(self):
        try:
            path = urlparse(self.path).path
            body = self.read_json()
            if path == '/api/login':
                username = str(body.get('username', '')).strip()
                password = str(body.get('password', ''))
                ip = self.client_ip()
                now = datetime.now()
                with SESSION_LOCK:
                    LOGIN_ATTEMPTS[ip] = [t for t in LOGIN_ATTEMPTS.get(ip, []) if now - t < timedelta(minutes=10)]
                    if len(LOGIN_ATTEMPTS[ip]) >= 10:
                        return self.error_json('تلاش ورود بیش از حد است؛ ده دقیقه بعد دوباره امتحان کنید', 429)
                with connect() as con:
                    row = con.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1', (username,)).fetchone()
                if not row or not pass_ok(password, row['password_hash']):
                    with SESSION_LOCK:
                        LOGIN_ATTEMPTS.setdefault(ip, []).append(now)
                    return self.error_json('نام کاربری یا رمز عبور اشتباه است', 401)
                with SESSION_LOCK:
                    LOGIN_ATTEMPTS.pop(ip, None)
                token = secrets.token_urlsafe(32)
                with SESSION_LOCK:
                    SESSIONS[token] = {'user_id': row['id'], 'expires': datetime.now() + timedelta(hours=12)}
                user = {k: row[k] for k in ('id','username','name','role')}
                return self.json_response({'ok': True, 'token': token, 'user': user})
            if path == '/api/logout':
                auth = self.headers.get('Authorization', '')
                token = auth[7:] if auth.startswith('Bearer ') else ''
                with SESSION_LOCK: SESSIONS.pop(token, None)
                return self.json_response({'ok': True})
            if path == '/api/products':
                if not self.auth(manager=True): return
                code, name = str(body.get('code','')).strip(), str(body.get('name','')).strip()
                if not code or not name: return self.error_json('کد و نام کالا الزامی است')
                with WRITE_LOCK, connect() as con:
                    try:
                        cur = con.execute('''INSERT INTO products(code,name,brand,size,carton_meterage,cartons_per_pallet,description)
                          VALUES(?,?,?,?,?,?,?)''', (code,name,str(body.get('brand','')).strip(),str(body.get('size','')).strip(),float(body.get('carton_meterage',0)),float(body.get('cartons_per_pallet',0)),str(body.get('description','')).strip()))
                    except sqlite3.IntegrityError:
                        return self.error_json('کد کالا تکراری است', 409)
                return self.json_response({'ok': True, 'id': cur.lastrowid}, 201)
            if path == '/api/entries':
                user = self.auth()
                if not user: return
                try:
                    product_id=int(body.get('product_id')); fp=float(body.get('full_pallets',0)); bc=float(body.get('broken_cartons',0))
                except Exception: return self.error_json('اطلاعات عددی معتبر نیست')
                grade=str(body.get('grade','')).strip(); date=str(body.get('production_date','')).strip(); jdate=str(body.get('jalali_date','')).strip()
                if not grade or not date or not jdate or fp<0 or bc<0: return self.error_json('اطلاعات ثبت کامل نیست')
                with WRITE_LOCK, connect() as con:
                    p=con.execute('SELECT * FROM products WHERE id=? AND active=1',(product_id,)).fetchone()
                    if not p: return self.error_json('کالا پیدا نشد',404)
                    cartons=round(fp*p['cartons_per_pallet']+bc,2); meterage=round(cartons*p['carton_meterage'],2)
                    cur=con.execute('''INSERT INTO entries(production_date,jalali_date,product_id,full_pallets,broken_cartons,grade,total_cartons,total_meterage,user_id,user_name,notes)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(date,jdate,product_id,fp,bc,grade,cartons,meterage,user['id'],user['name'],str(body.get('notes','')).strip()))
                return self.json_response({'ok':True,'id':cur.lastrowid,'total_cartons':cartons,'total_meterage':meterage},201)
            if path == '/api/users':
                if not self.auth(manager=True): return
                username,name,password,role=str(body.get('username','')).strip(),str(body.get('name','')).strip(),str(body.get('password','')),str(body.get('role','employee'))
                if not username or not name or not password or role not in ('manager','employee'): return self.error_json('اطلاعات کاربر کامل نیست')
                with WRITE_LOCK,connect() as con:
                    try: cur=con.execute('INSERT INTO users(username,password_hash,name,role) VALUES(?,?,?,?)',(username,pass_hash(password),name,role))
                    except sqlite3.IntegrityError:return self.error_json('نام کاربری تکراری است',409)
                return self.json_response({'ok':True,'id':cur.lastrowid},201)
            if path == '/api/restore':
                if not self.auth(manager=True): return
                backup=body.get('backup',{})
                if not isinstance(backup.get('products'),list) or not isinstance(backup.get('entries'),list): return self.error_json('فایل پشتیبان معتبر نیست')
                with WRITE_LOCK,connect() as con:
                    con.execute('BEGIN IMMEDIATE'); con.execute('DELETE FROM entries'); con.execute('DELETE FROM products')
                    for p in backup['products']:
                        con.execute('''INSERT INTO products(id,code,name,brand,size,carton_meterage,cartons_per_pallet,description,active,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(p.get('id'),p.get('code'),p.get('name'),p.get('brand',''),p.get('size',''),p.get('carton_meterage',0),p.get('cartons_per_pallet',0),p.get('description',p.get('desc','')),p.get('active',1),p.get('created_at',datetime.now().isoformat()),p.get('updated_at',datetime.now().isoformat())))
                    for e in backup['entries']:
                        con.execute('''INSERT INTO entries(id,production_date,jalali_date,product_id,full_pallets,broken_cartons,grade,total_cartons,total_meterage,user_id,user_name,notes,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(e.get('id'),e.get('production_date',e.get('date')),e.get('jalali_date',e.get('date_jalali','')),e.get('product_id'),e.get('full_pallets',0),e.get('broken_cartons',0),e.get('grade',''),e.get('total_cartons',0),e.get('total_meterage',0),None,e.get('user_name',e.get('user','')),e.get('notes',''),e.get('created_at',datetime.now().isoformat())))
                return self.json_response({'ok':True})
            return self.error_json('مسیر API پیدا نشد',404)
        except ValueError as e: self.error_json(str(e),400)
        except Exception as exc: self.error_json('خطای داخلی سرور: '+str(exc),500)

    def do_PUT(self):
        try:
            path=urlparse(self.path).path;body=self.read_json();parts=path.strip('/').split('/')
            if len(parts)!=3 or parts[0]!='api':return self.error_json('مسیر نامعتبر',404)
            kind,idv=parts[1],int(parts[2])
            if kind=='products':
                if not self.auth(manager=True):return
                with WRITE_LOCK,connect() as con:
                    try:con.execute('''UPDATE products SET code=?,name=?,brand=?,size=?,carton_meterage=?,cartons_per_pallet=?,description=?,updated_at=CURRENT_TIMESTAMP WHERE id=?''',(str(body.get('code','')).strip(),str(body.get('name','')).strip(),str(body.get('brand','')).strip(),str(body.get('size','')).strip(),float(body.get('carton_meterage',0)),float(body.get('cartons_per_pallet',0)),str(body.get('description','')).strip(),idv))
                    except sqlite3.IntegrityError:return self.error_json('کد کالا تکراری است',409)
                return self.json_response({'ok':True})
            if kind=='users':
                manager=self.auth(manager=True)
                if not manager:return
                username,name,role=str(body.get('username','')).strip(),str(body.get('name','')).strip(),str(body.get('role','employee'))
                if idv==manager['id'] and role!='manager':return self.error_json('نقش مدیر جاری قابل کاهش نیست')
                with WRITE_LOCK,connect() as con:
                    try:
                        if body.get('password'):con.execute('UPDATE users SET username=?,name=?,role=?,password_hash=? WHERE id=?',(username,name,role,pass_hash(str(body['password'])),idv))
                        else:con.execute('UPDATE users SET username=?,name=?,role=? WHERE id=?',(username,name,role,idv))
                    except sqlite3.IntegrityError:return self.error_json('نام کاربری تکراری است',409)
                return self.json_response({'ok':True})
            return self.error_json('مسیر نامعتبر',404)
        except Exception as exc:self.error_json('خطای داخلی سرور: '+str(exc),500)

    def do_DELETE(self):
        try:
            path=urlparse(self.path).path;parts=path.strip('/').split('/')
            if len(parts)!=3 or parts[0]!='api':return self.error_json('مسیر نامعتبر',404)
            kind,idv=parts[1],int(parts[2]);manager=self.auth(manager=True)
            if not manager:return
            with WRITE_LOCK,connect() as con:
                if kind=='entries':con.execute('DELETE FROM entries WHERE id=?',(idv,))
                elif kind=='products':
                    if con.execute('SELECT 1 FROM entries WHERE product_id=? LIMIT 1',(idv,)).fetchone():return self.error_json('این کالا سابقه تولید دارد و قابل حذف نیست',409)
                    con.execute('DELETE FROM products WHERE id=?',(idv,))
                elif kind=='users':
                    if idv==manager['id']:return self.error_json('نمی‌توانید حساب خودتان را حذف کنید')
                    con.execute('UPDATE users SET active=0 WHERE id=?',(idv,))
                else:return self.error_json('مسیر نامعتبر',404)
            return self.json_response({'ok':True})
        except Exception as exc:self.error_json('خطای داخلی سرور: '+str(exc),500)

    def serve_static(self, path):
        rel = 'index.html' if path in ('','/') else unquote(path.lstrip('/'))
        target = (STATIC / rel).resolve()
        if STATIC.resolve() not in target.parents and target != STATIC.resolve():
            return self.send_error(403)
        if not target.is_file():
            target = STATIC / 'index.html'
        data = target.read_bytes();ctype=mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        self.send_response(200);self.send_header('Content-Type',ctype + ('; charset=utf-8' if ctype.startswith('text/') else ''));self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-cache' if target.name=='index.html' else 'public, max-age=86400');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY');self.send_header('Referrer-Policy','same-origin');self.end_headers();self.wfile.write(data)


def main():
    init_db()
    server=ThreadingHTTPServer((HOST,PORT),Handler)
    print('='*62)
    print('سامانه مشترک ثبت تولید نوین اجرا شد')
    print(f'روی همین کامپیوتر: http://localhost:{PORT}')
    print(f'برای شبکه کارخانه: http://IP-این-کامپیوتر:{PORT}')
    print(f'نام کاربری مدیر: {ADMIN_USER}')
    print('رمز مدیر از متغیر NOVIN_ADMIN_PASSWORD خوانده می‌شود')
    print('برای توقف Ctrl+C را بزنید')
    print('='*62)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
