# scrabble - Developed by acidvegas in Python (https://github.com/acidvegas)
# auth.py

import math
import random
import re
import secrets
import threading
import time
from flask import request

import arcade
from storage import DB_LOCK, db_connect


CAPTCHA_TTL = 300
SESSION_TTL = 30 * 24 * 60 * 60
CAPTCHAS = {}
CAPTCHA_LOCK = threading.Lock()
USERNAME_RE = re.compile(r'^[A-Za-z0-9_]{3,16}$')
CAPTCHA_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no I/O/0/1
CAPTCHA_COLORS = ['#ff7da0', '#ffae41', '#4dd17a', '#5fa8ff', '#b07dff', '#ffd86a', '#ff5e8a', '#7da0ff']
ARCADE_POLL = 60
CAPTCHA_MAX = 5000 # captchas kept at once, the oldest are dropped past this
ATTEMPTS = {} # rate limit key -> recent attempt times
ATTEMPTS_LOCK = threading.Lock()
LIMITS = {'account': (10, 900), 'ip': (30, 900), 'captcha': (30, 60)} # kind -> (attempts, seconds)

# Captcha glyphs as strokes on a 10 wide, 14 tall grid (y down). They are drawn as warped SVG paths, so the answer is never in the markup as text.
GLYPHS = {
	'A': [[(0, 14), (5, 0), (10, 14)], [(2, 8.5), (8, 8.5)]],
	'B': [[(0, 0), (0, 14), (6.5, 14), (9, 12.5), (9, 9), (6.5, 7), (0, 7)], [(0, 0), (6, 0), (8.5, 1.5), (8.5, 5), (6, 7)]],
	'C': [[(9.5, 2), (7.5, 0), (3, 0), (0.5, 2.5), (0, 7), (0.5, 11.5), (3, 14), (7.5, 14), (9.5, 12)]],
	'D': [[(0, 0), (0, 14), (5, 14), (8.5, 11.5), (10, 7), (8.5, 2.5), (5, 0), (0, 0)]],
	'E': [[(10, 0), (0, 0), (0, 14), (10, 14)], [(0, 7), (7, 7)]],
	'F': [[(10, 0), (0, 0), (0, 14)], [(0, 7), (7, 7)]],
	'G': [[(9.5, 2), (7.5, 0), (3, 0), (0.5, 2.5), (0, 7), (0.5, 11.5), (3, 14), (7.5, 14), (10, 12), (10, 8), (6, 8)]],
	'H': [[(0, 0), (0, 14)], [(10, 0), (10, 14)], [(0, 7), (10, 7)]],
	'J': [[(10, 0), (10, 10.5), (8, 14), (3, 14), (0, 11)]],
	'K': [[(0, 0), (0, 14)], [(10, 0), (0, 8)], [(3, 6), (10, 14)]],
	'L': [[(0, 0), (0, 14), (10, 14)]],
	'M': [[(0, 14), (0, 0), (5, 8), (10, 0), (10, 14)]],
	'N': [[(0, 14), (0, 0), (10, 14), (10, 0)]],
	'P': [[(0, 14), (0, 0), (7, 0), (10, 2), (10, 5.5), (7, 7.5), (0, 7.5)]],
	'Q': [[(5, 0), (1.5, 1.5), (0, 7), (1.5, 12.5), (5, 14), (8.5, 12.5), (10, 7), (8.5, 1.5), (5, 0)], [(6, 10), (10, 14.5)]],
	'R': [[(0, 14), (0, 0), (7, 0), (10, 2), (10, 5.5), (7, 7.5), (0, 7.5)], [(5, 7.5), (10, 14)]],
	'S': [[(9.5, 2), (7, 0), (3, 0), (0.5, 2), (0.5, 5), (3, 6.8), (7, 7.2), (9.5, 9), (9.5, 12), (7, 14), (3, 14), (0.5, 12)]],
	'T': [[(0, 0), (10, 0)], [(5, 0), (5, 14)]],
	'U': [[(0, 0), (0, 10.5), (2, 14), (8, 14), (10, 10.5), (10, 0)]],
	'V': [[(0, 0), (5, 14), (10, 0)]],
	'W': [[(0, 0), (2.5, 14), (5, 6), (7.5, 14), (10, 0)]],
	'X': [[(0, 0), (10, 14)], [(10, 0), (0, 14)]],
	'Y': [[(0, 0), (5, 7), (10, 0)], [(5, 7), (5, 14)]],
	'Z': [[(0, 0), (10, 0), (0, 14), (10, 14)]],
	'2': [[(0.5, 3), (2.5, 0.3), (7, 0.3), (9.5, 2.5), (9.5, 5), (0, 14), (10, 14)]],
	'3': [[(0.5, 1.5), (3, 0), (7, 0), (9.5, 2), (9.5, 5), (7, 7), (4, 7)], [(7, 7), (9.8, 9), (9.8, 12), (7, 14), (3, 14), (0.5, 12.5)]],
	'4': [[(7.5, 14), (7.5, 0), (0, 10), (10, 10)]],
	'5': [[(9.5, 0), (1, 0), (0.5, 6), (4, 5.5), (7.5, 6), (10, 8.5), (10, 11.5), (7.5, 14), (3, 14), (0.5, 12.5)]],
	'6': [[(9, 1), (6.5, 0), (3, 0), (0.5, 3), (0, 8), (0.5, 12), (3, 14), (7, 14), (9.8, 12), (9.8, 9), (7, 7), (3, 7), (0.3, 9)]],
	'7': [[(0, 0), (10, 0), (4, 14)]],
	'8': [[(5, 7), (1.5, 5.5), (1, 2.5), (3, 0), (7, 0), (9, 2.5), (8.5, 5.5), (5, 7), (1, 9), (0.5, 12), (3, 14), (7, 14), (9.5, 12), (9, 9), (5, 7)]],
	'9': [[(9.7, 5), (7, 7), (3, 7), (0.3, 5), (0.3, 2), (3, 0), (7, 0), (9.7, 2), (10, 6), (9.5, 11), (7, 14), (3.5, 14), (1, 13)]],
}


def arcade_free_username(c, username: str, user_id: int):
	'''Give any other mirror row still holding this username (a stale rename) a placeholder until its own update arrives.'''

	c.execute("UPDATE users SET username = '#' || id WHERE username=? AND id!=?", (username, user_id))


def arcade_mirror(user_id: int, username: str):
	'''Create or rename the local mirror row of an acid-arcade-db account. A new row also gets what local signup creates (a user_stats row).'''

	with DB_LOCK:
		with db_connect() as c:
			row = c.execute('SELECT username FROM users WHERE id=?', (user_id,)).fetchone()
			if row and row['username'] == username:
				return
			arcade_free_username(c, username, user_id)
			if row:
				c.execute('UPDATE users SET username=? WHERE id=?', (username, user_id))
			else:
				c.execute("INSERT INTO users(id, username, password_hash, created_at) VALUES(?, ?, '', ?)", (user_id, username, int(time.time())))
				c.execute('INSERT OR IGNORE INTO user_stats(user_id) VALUES(?)', (user_id,))
			c.commit()


def arcade_sync(since: float) -> float:
	'''Apply one acid-arcade-db changes poll (renames and deletes) to the local database and return the next since.'''

	data = arcade.changes(since)
	with DB_LOCK:
		with db_connect() as c:
			for user in data.get('changed', []):
				arcade_free_username(c, user['username'], user['user_id'])
				c.execute('UPDATE users SET username=? WHERE id=?', (user['username'], user['user_id']))
			for user_id in data.get('deleted', []):
				for table, col in (('sessions', 'user_id'), ('user_word_counts', 'user_id'), ('user_stats', 'user_id'), ('users', 'id')):
					c.execute(f'DELETE FROM {table} WHERE {col}=?', (user_id,))
			c.commit()
	return data['now']


def arcade_sync_loop():
	'''Poll acid-arcade-db for account changes forever.'''

	since = 0
	while True:
		try:
			since = arcade_sync(since)
		except Exception as e:
			print(f'arcade_sync error: {e}')
		time.sleep(ARCADE_POLL)


def attempt(kind: str, key: str):
	'''Record an attempt against a rate limit.'''

	with ATTEMPTS_LOCK:
		ATTEMPTS.setdefault(f'{kind}:{key}', []).append(time.time())


def blocked(kind: str, key: str) -> bool:
	'''Return True if a rate limit is used up. Also drops attempts that are too old to count.'''

	limit, window = LIMITS[kind]
	now = time.time()
	with ATTEMPTS_LOCK:
		for k in [k for k, times in ATTEMPTS.items() if not times or now - times[-1] > LIMITS[k.split(':', 1)[0]][1]]: # each key by its own limit's window, or a short window (captchas) would wipe login failures
			del ATTEMPTS[k]
		times = ATTEMPTS.get(f'{kind}:{key}', [])
		times[:] = [t for t in times if now - t < window]
		return len(times) >= limit


def captcha_render_svg(text: str) -> str:
	'''Render captcha text as a noisy SVG. Each character is drawn from GLYPHS as jittered, rotated path strokes in a random order, so there is no text to read out of the markup.'''

	w, h  = 220, 80
	parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">', f'<rect width="{w}" height="{h}" fill="#1a1a28" rx="8"/>']
	for _ in range(40):
		parts.append(f'<circle cx="{random.randint(0, w)}" cy="{random.randint(0, h)}" r="{random.uniform(0.6, 1.8):.1f}" fill="{random.choice(CAPTCHA_COLORS)}" fill-opacity="0.4"/>')
	strokes = []
	slot    = (w - 30) / len(text)
	for i, ch in enumerate(text):
		cx, cy = 15 + slot * (i + 0.5) + random.uniform(-3, 3), h / 2 + random.uniform(-5, 5)
		scale  = random.uniform(2.2, 2.7)
		turn   = math.radians(random.uniform(-22, 22))
		lean   = random.uniform(-0.25, 0.25)
		color  = random.choice(CAPTCHA_COLORS)
		width  = random.uniform(2.6, 3.6)
		for line in GLYPHS[ch]:
			pts = []
			for x, y in line:
				x, y = (x - 5 + random.uniform(-0.6, 0.6) + lean * (y - 7)) * scale, (y - 7 + random.uniform(-0.6, 0.6)) * scale
				pts.append((cx + x * math.cos(turn) - y * math.sin(turn), cy + x * math.sin(turn) + y * math.cos(turn)))
			strokes.append((pts, color, width))
	for _ in range(6): # decoy strokes in the same colors and widths, so stroke style alone can't separate them from the letters
		x, y = random.uniform(0, w), random.uniform(10, h - 10)
		pts  = [(x, y)]
		for _ in range(random.randint(1, 3)):
			x, y = x + random.uniform(-22, 22), y + random.uniform(-16, 16)
			pts.append((x, y))
		strokes.append((pts, random.choice(CAPTCHA_COLORS), random.uniform(1.4, 3.0)))
	random.shuffle(strokes)
	for pts, color, width in strokes:
		d = 'M' + ' L'.join(f'{x:.1f} {y:.1f}' for x, y in pts)
		parts.append(f'<path d="{d}" stroke="{color}" stroke-width="{width:.1f}" stroke-linecap="round" stroke-linejoin="round" fill="none"/>')
	pts = ' '.join(f'{x},{h / 2 + random.uniform(-18, 18):.1f}' for x in range(0, w + 1, 12))
	parts.append(f'<polyline points="{pts}" stroke="{random.choice(CAPTCHA_COLORS)}" stroke-opacity="0.5" stroke-width="2" fill="none"/></svg>')
	return ''.join(parts)


def captcha_create():
	cid = secrets.token_urlsafe(12)
	text = ''.join(secrets.choice(CAPTCHA_CHARS) for _ in range(5))
	expires = time.time() + CAPTCHA_TTL
	with CAPTCHA_LOCK:
		# Sweep expired, then drop the oldest past CAPTCHA_MAX
		now = time.time()
		for k in list(CAPTCHAS.keys()):
			if CAPTCHAS[k][1] < now:
				del CAPTCHAS[k]
		for k in list(CAPTCHAS)[:max(0, len(CAPTCHAS) - CAPTCHA_MAX + 1)]:
			del CAPTCHAS[k]
		CAPTCHAS[cid] = (text, expires)
	return {'captcha_id': cid, 'svg': captcha_render_svg(text)}


def captcha_verify(cid, answer):
	if not cid or answer is None:
		return False
	with CAPTCHA_LOCK:
		rec = CAPTCHAS.pop(cid, None)
	if rec is None:
		return False
	correct, expires = rec
	if time.time() > expires:
		return False
	return str(answer).strip().upper() == correct.upper()


def create_session(user_id):
	token = secrets.token_urlsafe(32)
	now = int(time.time())
	expires = now + SESSION_TTL
	with DB_LOCK:
		with db_connect() as c:
			c.execute('INSERT INTO sessions(token, user_id, created_at, expires_at) VALUES(?, ?, ?, ?)',
					  (token, user_id, now, expires))
			c.commit()
	return token


def get_user_id_from_token(token):
	if not token:
		return None
	with DB_LOCK:
		with db_connect() as c:
			row = c.execute('SELECT user_id, expires_at FROM sessions WHERE token=?', (token,)).fetchone()
	if not row:
		return None
	if row['expires_at'] < time.time():
		return None
	return row['user_id']


def get_username(user_id):
	if user_id is None:
		return None
	with db_connect() as c:
		row = c.execute('SELECT username FROM users WHERE id=?', (user_id,)).fetchone()
	return row['username'] if row else None


def get_request_user_id():
	token = request.headers.get('X-Auth-Token')
	if not token and request.method != 'GET':
		token = (request.get_json(silent=True) or {}).get('auth_token')
	if arcade.enabled():
		info = arcade.session(token)
		if not info:
			return None
		arcade_mirror(info['user_id'], info['username'])
		return info['user_id']
	return get_user_id_from_token(token)
