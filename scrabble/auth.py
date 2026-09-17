# scrabble - Developed by acidvegas in Python (https://github.com/acidvegas)
# auth.py

import re
import secrets
import threading
import time
from flask import request

from storage import DB_LOCK, db_connect


CAPTCHA_TTL = 300
SESSION_TTL = 30 * 24 * 60 * 60
CAPTCHAS = {}
CAPTCHA_LOCK = threading.Lock()
USERNAME_RE = re.compile(r'^[A-Za-z0-9_]{3,16}$')
CAPTCHA_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'  # no I/O/0/1
CAPTCHA_COLORS = ['#ff7da0', '#ffae41', '#4dd17a', '#5fa8ff', '#b07dff', '#ffd86a', '#ff5e8a', '#7da0ff']


def captcha_render_svg(text):
	import random as _r
	W, H = 220, 80
	parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">']
	parts.append(f'<rect width="{W}" height="{H}" fill="#1a1a28" rx="8"/>')
	# Background noise lines
	for _ in range(8):
		x1 = _r.randint(0, W); y1 = _r.randint(0, H)
		x2 = _r.randint(0, W); y2 = _r.randint(0, H)
		col = _r.choice(CAPTCHA_COLORS)
		parts.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{col}" stroke-opacity="0.18" stroke-width="1.5"/>')
	# Dots
	for _ in range(40):
		x = _r.randint(0, W); y = _r.randint(0, H)
		r = _r.uniform(0.6, 1.8)
		col = _r.choice(CAPTCHA_COLORS)
		parts.append(f'<circle cx="{x}" cy="{y}" r="{r:.1f}" fill="{col}" fill-opacity="0.4"/>')
	# Characters
	n = len(text)
	slot_w = (W - 30) / n
	for i, ch in enumerate(text):
		cx = 18 + slot_w * (i + 0.5) + _r.uniform(-4, 4)
		cy = H / 2 + _r.uniform(-6, 6)
		rot = _r.uniform(-25, 25)
		size = _r.randint(30, 40)
		col = _r.choice(CAPTCHA_COLORS)
		weight = _r.choice([700, 800, 900])
		parts.append(
			f'<text x="{cx:.1f}" y="{cy:.1f}" font-family="Courier New, monospace" font-weight="{weight}" '
			f'font-size="{size}" fill="{col}" text-anchor="middle" dominant-baseline="middle" '
			f'transform="rotate({rot:.1f} {cx:.1f} {cy:.1f})">{ch}</text>'
		)
	# Wavy overlay line
	pts = []
	for x in range(0, W + 1, 12):
		y = H / 2 + _r.uniform(-18, 18)
		pts.append(f'{x},{y:.1f}')
	parts.append(f'<polyline points="{" ".join(pts)}" stroke="{_r.choice(CAPTCHA_COLORS)}" stroke-opacity="0.5" stroke-width="2" fill="none"/>')
	parts.append('</svg>')
	return ''.join(parts)


def captcha_create():
	cid = secrets.token_urlsafe(12)
	text = ''.join(secrets.choice(CAPTCHA_CHARS) for _ in range(5))
	expires = time.time() + CAPTCHA_TTL
	with CAPTCHA_LOCK:
		# Sweep expired
		now = time.time()
		for k in list(CAPTCHAS.keys()):
			if CAPTCHAS[k][1] < now:
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
	return get_user_id_from_token(token)
