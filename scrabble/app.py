# scrabble - Developed by acidvegas in Python (https://github.com/acidvegas)
# app.py

import atexit
import functools
import json
import os
import queue
import random
import secrets
import sqlite3
import threading
import time
from flask import Flask, Response, render_template, request, jsonify, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash

import arcade
import move_gen
from auth import USERNAME_RE, arcade_sync_loop, attempt, blocked, captcha_create, captcha_verify, create_session, get_request_user_id, get_username
from game import AI_TOKEN, MAX_PLAYERS_CAP, MIN_PLAYERS, SPECTATOR_SLOT, SPEED_MAX_TURNS, SPEED_TURN_SECONDS, advance_turn, after_action_speed, ai_take_turn, apply_play, check_placement, check_speed_timeout, clean_name, clean_placements, end_turn, filled_slots, finalize_game, get_ai_slot, new_game, new_room, placement_words, player_has_moves, rack_value, record_history, set_last_action, touch_room, unseen_counts, highest_active_other
from storage import DB_LOCK, HINT_TOKEN_CAP, WINS_PER_TOKEN, backup_loop, db_connect, delete_room_persisted, get_hint_tokens, init_db, load_rooms, meta_get, prune_sessions, run_daily_backup, save_room
from words import DICTIONARY, WORD_TRIE, check_dictionary, fetch_definition


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1 if arcade.enabled() else 0, x_proto=1) # https links in the page metadata behind nginx, and in arcade mode the player's IP for acid-arcade-db rate limits
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 # no request needs more than a few KB

CACHE_VERSION = int(time.time())
PUBLIC_URL = os.environ.get('SCRABBLE_PUBLIC_URL', '').rstrip('/') # canonical site for crawlers and share cards, e.g. https://scrabble.acid.vegas (unset: the request host)

ROOMS_LOCK = threading.RLock()
MAX_ROOMS = 100
MAX_ROOMS_PER_IP = 5 # open (not game over) rooms one IP may have created at once
ROOM_TIMEOUT_SECONDS = 24 * 60 * 60
CHAT_MAX = 100
CHAT_TEXT_MAX = 100
CHAT_RATE_WINDOW = 1.0
CHAT_RATE_MAX = 2
MAX_SPECTATORS = 50 # watchers kept per room, the oldest is dropped past this
MAX_SPECTATORS_PER_IP = 10 # watchers one IP keeps per room, its oldest is dropped past this


def reap_old_rooms():
	now = time.time()
	stale = [rid for rid, room in rooms.items() if now - room.get('last_activity', room.get('created_at', now)) > ROOM_TIMEOUT_SECONDS]
	if not stale:
		return
	for rid in stale:
		room = rooms.get(rid)
		if room and not room['game']['game_over']:
			try:
				finalize_game(room, 'timeout')
			except Exception as e:
				print(f'timeout finalize failed for {rid}: {e}')
		rooms.pop(rid, None)
		delete_room_persisted(rid)


def reaper_loop():
	while True:
		time.sleep(300)
		try:
			with ROOMS_LOCK:
				reap_old_rooms()
			prune_sessions()
		except Exception as e:
			print(f'reaper_loop error: {e}')


def save_rooms():
	for rid, room in list(rooms.items()):
		save_room(rid, room)


init_db()
rooms = load_rooms()
reap_old_rooms()
atexit.register(save_rooms)
threading.Thread(target=reaper_loop, daemon=True).start()
try:
	run_daily_backup()
except Exception as e:
	print(f'initial backup failed: {e}')
threading.Thread(target=backup_loop, daemon=True).start()
if arcade.enabled():
	threading.Thread(target=arcade_sync_loop, daemon=True).start()


@app.errorhandler(arcade.ArcadeDown)
def arcade_down(e):
	'''Fail hard when acid-arcade-db is unreachable instead of treating the player as a guest.'''

	return jsonify({'error': 'account server is down'}), 503


@app.errorhandler(arcade.ArcadeError)
def arcade_error(e):
	'''Pass acid-arcade-db refusals (bad login, taken name, wrong captcha) through to the player.'''

	return jsonify({'error': str(e)}), e.status


def arcade_public_url() -> str:
	'''Return the acid-arcade-db site on this request's domain for the frontend, or an empty string in standalone mode.'''

	return arcade.public_url(request.host) if arcade.enabled() else ''


def arcade_session_reply(d: dict):
	'''Answer an acid-arcade-db sign-in like a local login, and share it with the other sites on this domain.'''

	resp = jsonify({'auth_token': d['token'], 'username': d['username'], 'user_id': d['user_id']})
	arcade.set_cookie(resp, request.host, d['token'])
	return resp


def site_url() -> str:
	'''Return the absolute site root (with a trailing slash) for canonical and share links: SCRABBLE_PUBLIC_URL, or the request host.'''

	return f'{PUBLIC_URL}/' if PUBLIC_URL else request.url_root


def structured_data(url: str) -> str:
	'''Return the JSON-LD description of the game for search engines.'''

	return json.dumps({
		'@context'            : 'https://schema.org',
		'@type'               : ['VideoGame', 'WebApplication'],
		'name'                : 'Scrabble',
		'url'                 : url,
		'description'         : 'Play Scrabble online for free with 2 to 4 players. Create a room and share the code, play the computer, or watch a game.',
		'image'               : f'{url}static/og.png',
		'applicationCategory' : 'GameApplication',
		'operatingSystem'     : 'Any',
		'genre'               : ['Word game', 'Board game'],
		'gamePlatform'        : 'Web browser',
		'playMode'            : ['SinglePlayer', 'MultiPlayer'],
		'isAccessibleForFree' : True,
		'offers'              : {'@type': 'Offer', 'price': '0', 'priceCurrency': 'USD'},
		'author'              : {'@type': 'Person', 'name': 'acidvegas', 'url': 'https://github.com/acidvegas'},
	})


def current_names(record: dict) -> dict:
	'''In arcade mode, show the current username of the accounts in a leaderboard record (renames) and drop ids of deleted accounts.'''

	if record and arcade.enabled():
		for name_key, id_key in (('player', 'user_id'), ('opponent', 'opponent_user_id')):
			if record.get(id_key) is None:
				continue
			name = get_username(record[id_key])
			if name:
				record[name_key] = name
			else:
				record[id_key] = None
	return record


SUBSCRIBERS = {}
SUBSCRIBERS_LOCK = threading.Lock()
STREAM_USERS = {} # open event stream queue -> signed in user id, for arcade presence
STREAM_IPS = {} # client IP -> open event stream count
MAX_STREAMS_PER_IP = 20 # concurrent /events streams one IP may hold
PRESENCE_INTERVAL = 30


def report_presence():
	'''Tell acid-arcade-db which signed in players have a room open right now (an event stream), printing instead of raising on failure.'''

	with SUBSCRIBERS_LOCK:
		user_ids = sorted(set(STREAM_USERS.values()))
	try:
		arcade.presence(user_ids)
	except Exception as e:
		print(f'arcade presence failed: {e}')


def presence_loop():
	'''Report presence to acid-arcade-db forever.'''

	while True:
		report_presence()
		time.sleep(PRESENCE_INTERVAL)


if arcade.enabled():
	threading.Thread(target=presence_loop, daemon=True).start()


def notify_room(rid):
	if not rid:
		return
	with SUBSCRIBERS_LOCK:
		subs = list(SUBSCRIBERS.get(rid, []))
	for q in subs:
		try:
			q.put_nowait('change')
		except Exception:
			pass


def rooms_locked(fn):
	"""Run a route while holding ROOMS_LOCK so room state changes don't interleave."""
	@functools.wraps(fn)
	def wrapper(*args, **kwargs):
		with ROOMS_LOCK:
			return fn(*args, **kwargs)
	return wrapper


@app.route('/events')
def events():
	with ROOMS_LOCK:
		room, _game, _slot = get_ctx()
		if room is None:
			return Response('event: error\ndata: invalid_room\n\n', mimetype='text/event-stream', status=404)
		rid = room['_rid']
		user_ids = room.get('user_ids') or []
		uid = user_ids[_slot] if 0 <= _slot < len(user_ids) else None
	ip = request.remote_addr
	q = queue.Queue(maxsize=64)
	with SUBSCRIBERS_LOCK:
		if STREAM_IPS.get(ip, 0) >= MAX_STREAMS_PER_IP:
			return Response('event: error\ndata: too_many_streams\n\n', mimetype='text/event-stream', status=429)
		STREAM_IPS[ip] = STREAM_IPS.get(ip, 0) + 1
		SUBSCRIBERS.setdefault(rid, []).append(q)
		if uid is not None:
			STREAM_USERS[q] = uid

	def stream():
		try:
			yield ': ' + (' ' * 2048) + '\n\n'
			yield 'retry: 3000\n\n'
			yield 'event: connected\ndata: ok\n\n'
			while True:
				try:
					msg = q.get(timeout=10)
					yield f'event: state\ndata: {msg}\n\n'
				except queue.Empty:
					yield ': ping\n\n'
		except GeneratorExit:
			pass
		finally:
			with SUBSCRIBERS_LOCK:
				STREAM_USERS.pop(q, None)
				subs = SUBSCRIBERS.get(rid, [])
				if q in subs:
					subs.remove(q)
				if not subs:
					SUBSCRIBERS.pop(rid, None)

	def release_ip():
		with SUBSCRIBERS_LOCK:
			STREAM_IPS[ip] -= 1
			if STREAM_IPS[ip] <= 0:
				STREAM_IPS.pop(ip, None)

	resp = Response(stream(), mimetype='text/event-stream', headers={
		'Content-Type': 'text/event-stream; charset=utf-8',
		'Cache-Control': 'no-cache, no-transform, no-store',
		'X-Accel-Buffering': 'no',
		'Connection': 'keep-alive',
	})
	resp.call_on_close(release_ip) # runs when the server closes the response, even if the stream never started or raised
	return resp


ROOM_ID_CHARS = 'abcdefghjkmnpqrstuvwxyz23456789'
ROOM_ID_LEN = 4


def make_room_id():
	while True:
		rid = ''.join(random.choices(ROOM_ID_CHARS, k=ROOM_ID_LEN))
		if rid not in rooms:
			return rid


def get_ctx():
	if request.method == 'GET':
		rid = (request.args.get('room_id') or '').lower()
		token = request.headers.get('X-Player-Token') or request.args.get('player_token') or ''
	else:
		data = request.get_json(silent=True) or {}
		rid = (data.get('room_id') or '').lower()
		token = data.get('player_token') or ''
	if not rid or rid not in rooms:
		return None, None, None
	room = rooms[rid]
	if token == AI_TOKEN:
		return None, None, None
	slot = room['players'].get(token)
	if slot is None:
		if token and token in room.get('spectators', {}):
			room['_rid'] = rid
			return room, room['game'], SPECTATOR_SLOT
		return None, None, None
	room['_rid'] = rid
	return room, room['game'], slot


def maybe_run_ai(room):
	"""If it's the AI's turn and the think-delay has elapsed, take the turn.

	Callers hold ROOMS_LOCK, so concurrent /state polls can't both fire a turn.
	"""
	if not room:
		return False
	game = room['game']
	if not game.get('vs_computer'):
		return False
	if game['game_over']:
		return False
	ai_slot = get_ai_slot(game)
	if ai_slot is None or game['current'] != ai_slot:
		return False
	if time.time() < game.get('ai_play_after', 0):
		return False
	rid = room.get('_rid')
	ai_take_turn(room)
	touch_room(room)
	save_room(rid, room)
	notify_room(rid)
	return True


@app.route('/')
def index():
	return render_template('index.html', cache_v=CACHE_VERSION, url=site_url(), room=False, arcade_public_url=arcade_public_url(), structured_data=structured_data(site_url()))


@app.route('/<rid>')
def index_with_room(rid):
	if len(rid) != ROOM_ID_LEN or not all(c in ROOM_ID_CHARS for c in rid.lower()):
		return 'Not found', 404
	return render_template('index.html', cache_v=CACHE_VERSION, url=site_url(), room=True, arcade_public_url=arcade_public_url())


@app.route('/favicon.ico')
def favicon():
	"""Serve the PNG icon for clients that only look for /favicon.ico."""

	return send_from_directory(app.static_folder, 'icon-192.png', mimetype='image/png')


@app.route('/robots.txt')
def robots():
	"""Let crawlers in and point them at the sitemap."""

	return Response(f'User-agent: *\nAllow: /\nSitemap: {site_url()}sitemap.xml\n', mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap():
	"""List the home page, the only page worth indexing."""

	return Response(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{site_url()}</loc></url></urlset>\n', mimetype='application/xml')


@app.route('/room_exists/<rid>')
@rooms_locked
def room_exists(rid):
	rid = rid.lower()
	if rid not in rooms:
		return jsonify({'exists': False})
	room = rooms[rid]
	max_players = room['game'].get('max_players', 2)
	filled = len(filled_slots(room))
	return jsonify({
		'exists': True,
		'full': filled >= max_players,
		'game_over': room['game']['game_over'],
		'player_count': filled,
		'max_players': max_players,
	})


@app.after_request
def no_cache(response):
	if request.path == '/' or request.path.startswith('/static/'):
		response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
		response.headers['Pragma'] = 'no-cache'
		response.headers['Expires'] = '0'
	return response


@app.route('/create_room', methods=['POST'])
@rooms_locked
def create_room():
	reap_old_rooms()
	if len(rooms) >= MAX_ROOMS:
		return jsonify({'error': f'Server full (max {MAX_ROOMS} concurrent rooms). Try again later.'}), 503
	ip = request.remote_addr
	if sum(1 for r in rooms.values() if r.get('creator_ip') == ip and not r['game']['game_over']) >= MAX_ROOMS_PER_IP:
		return jsonify({'error': f'you already have {MAX_ROOMS_PER_IP} open rooms, finish or leave one first'}), 429
	data = request.get_json(silent=True) or {}
	user_id = get_request_user_id()
	name = clean_name(data.get('name')) or 'P1'
	mode = data.get('mode')
	if mode not in ('classic', 'random'):
		mode = 'classic'
	speed = bool(data.get('speed', False))
	challenge_mode = bool(data.get('challenge_mode', False))
	transparent_mode = bool(data.get('transparent_mode', False))
	vs_computer = bool(data.get('vs_computer', False))
	try:
		ai_difficulty = int(data.get('ai_difficulty') or 50)
	except (TypeError, ValueError):
		ai_difficulty = 50
	ai_difficulty = max(1, min(100, ai_difficulty))
	ai_style = data.get('ai_style') if data.get('ai_style') in ('score', 'stacker') else 'score'
	if ai_style == 'stacker':
		ai_difficulty = 100  # Stacker is always max difficulty
	try:
		max_players = int(data.get('max_players') or 2)
	except (TypeError, ValueError):
		max_players = 2
	max_players = max(MIN_PLAYERS, min(MAX_PLAYERS_CAP, max_players))
	rid = make_room_id()
	rooms[rid] = new_room(mode=mode, speed=speed, max_players=max_players, challenge_mode=challenge_mode,
						   transparent_mode=transparent_mode, vs_computer=vs_computer, ai_difficulty=ai_difficulty,
						   ai_style=ai_style)
	rooms[rid]['_rid'] = rid
	rooms[rid]['creator_ip'] = ip
	token = secrets.token_urlsafe(16)
	rooms[rid]['players'][token] = 0
	if user_id is not None:
		uname = get_username(user_id)
		if uname:
			name = uname
		rooms[rid]['user_ids'][0] = user_id
	rooms[rid]['names'][0] = name
	if vs_computer:
		ai_slot = get_ai_slot(rooms[rid]['game'])
		rooms[rid]['players'][AI_TOKEN] = ai_slot
		rooms[rid]['names'][ai_slot] = 'STACKER' if ai_style == 'stacker' else 'COMPUTER'
		# For 2-player AI rooms (1 human + AI), start immediately.
		# For 3+ player AI rooms, wait until other humans join (same as regular multiplayer).
		if max_players == 2:
			rooms[rid]['game']['started_at'] = time.time()
	touch_room(rooms[rid])
	save_room(rid, rooms[rid])
	notify_room(rid)
	return jsonify({'room_id': rid, 'player_token': token, 'player_slot': 0, 'max_players': rooms[rid]['max_players']})


@app.route('/join_room', methods=['POST'])
@rooms_locked
def join_room():
	data = request.get_json(silent=True) or {}
	rid = (data.get('room_id') or '').lower().strip()
	raw_name = clean_name(data.get('name'))
	if not rid or rid not in rooms:
		return jsonify({'error': 'Room not found'}), 404
	room = rooms[rid]
	max_players = room['game'].get('max_players', 2)
	user_id = get_request_user_id()
	# If this signed-in user is already a member of the room from another device,
	# reissue a fresh token pointing to their existing slot instead of a new slot.
	if user_id is not None:
		for i, uid in enumerate(room.get('user_ids') or []):
			if uid == user_id and i < max_players:
				token = secrets.token_urlsafe(16)
				room['players'][token] = i
				touch_room(room)
				save_room(room.get('_rid'), room)
				notify_room(room.get('_rid'))
				return jsonify({'room_id': rid, 'player_token': token, 'player_slot': i, 'max_players': max_players})
	# Full check: count unique occupied slots (a single slot can be held by multiple tokens for multi-device).
	occupied_slots = filled_slots(room)
	if len(occupied_slots) >= max_players:
		return jsonify({'error': 'Room is full'}), 403
	slot = next(i for i in range(max_players) if i not in occupied_slots)
	name = raw_name or f'P{slot + 1}'
	token = secrets.token_urlsafe(16)
	room['players'][token] = slot
	if user_id is not None:
		uname = get_username(user_id)
		if uname:
			name = uname
		room.setdefault('user_ids', [None] * max_players)
		if len(room['user_ids']) < max_players:
			room['user_ids'].extend([None] * (max_players - len(room['user_ids'])))
		room['user_ids'][slot] = user_id
	room['names'][slot] = name
	if room['game']['started_at'] is None:
		room['game']['started_at'] = time.time()
	if room['game'].get('speed_mode') and not room['game'].get('turn_deadline') and len(filled_slots(room)) >= max_players:
		room['game']['turn_deadline'] = time.time() + SPEED_TURN_SECONDS
	touch_room(room)
	save_room(room.get('_rid'), room)
	notify_room(room.get('_rid'))
	return jsonify({'room_id': rid, 'player_token': token, 'player_slot': slot, 'max_players': max_players})


@app.route('/share_pending', methods=['POST'])
@rooms_locked
def share_pending():
	room, game, slot = get_ctx()
	if game is None:
		return jsonify({'error': 'invalid_room'}), 404
	if slot == SPECTATOR_SLOT:
		return jsonify({'error': 'spectator'}), 403
	if not game.get('transparent_mode'):
		return jsonify({'ok': True})
	data = request.get_json(silent=True) or {}
	raw = data.get('placements') or []
	clean = []
	seen = set()
	for p in raw if isinstance(raw, list) else []:
		if len(clean) >= 7: # a rack never holds more than 7 tiles
			break
		try:
			r = int(p.get('row'))
			c = int(p.get('col'))
		except (AttributeError, TypeError, ValueError):
			continue
		if not (0 <= r < 15 and 0 <= c < 15) or (r, c) in seen:
			continue
		letter = (p.get('letter') or '').upper()[:1]
		if not letter or not letter.isalpha():
			continue
		seen.add((r, c))
		clean.append({'row': r, 'col': c, 'letter': letter, 'blank': bool(p.get('blank'))})
	game.setdefault('pending_views', {})
	key = str(slot)
	if clean:
		game['pending_views'][key] = clean
	else:
		game['pending_views'].pop(key, None)
	notify_room(room.get('_rid'))
	return jsonify({'ok': True})


@app.route('/watch_room', methods=['POST'])
@rooms_locked
def watch_room():
	data = request.get_json(silent=True) or {}
	rid = (data.get('room_id') or '').lower().strip()
	raw_name = clean_name(data.get('name'))
	if not rid or rid not in rooms:
		return jsonify({'error': 'Room not found'}), 404
	room = rooms[rid]
	room.setdefault('spectators', {})
	ips = room.setdefault('spectator_ips', {})
	ip = request.remote_addr
	mine = [t for t in room['spectators'] if ips.get(t) == ip]
	for old in mine[:max(0, len(mine) - MAX_SPECTATORS_PER_IP + 1)] + list(room['spectators'])[:max(0, len(room['spectators']) - MAX_SPECTATORS + 1)]:
		room['spectators'].pop(old, None) # tokens are minted freely, so the oldest watchers make room instead of the list growing forever
		ips.pop(old, None)
	name = raw_name or 'Watcher'
	token = secrets.token_urlsafe(16)
	room['spectators'][token] = name
	ips[token] = ip
	touch_room(room)
	save_room(room.get('_rid'), room)
	notify_room(room.get('_rid'))
	return jsonify({'room_id': rid, 'player_token': token, 'spectator': True})


@app.route('/set_name', methods=['POST'])
@rooms_locked
def set_name():
	room, game, slot = get_ctx()
	if room is None:
		return jsonify({'error': 'invalid_room'}), 404
	data = request.get_json(silent=True) or {}
	name = clean_name(data.get('name'))
	if not name:
		return jsonify({'error': 'Name must be letters only (1-9 chars)'}), 400
	if slot == SPECTATOR_SLOT:
		token = (request.get_json(silent=True) or {}).get('player_token') or ''
		if token in room.get('spectators', {}):
			room['spectators'][token] = name
		touch_room(room)
		save_room(room.get('_rid'), room)
		return jsonify({'ok': True, 'name': name})
	room['names'][slot] = name
	touch_room(room)
	save_room(room.get('_rid'), room)
	notify_room(room.get('_rid'))
	return jsonify({'ok': True, 'name': name})


@app.route('/state')
@rooms_locked
def state():
	room, game, slot = get_ctx()
	if game is None:
		return jsonify({'error': 'invalid_room'}), 404
	check_speed_timeout(room)
	maybe_run_ai(room)
	touch_room(room)
	is_spectator = slot == SPECTATOR_SLOT
	no_moves = False
	if (not is_spectator) and (not game['game_over']) and slot == game['current'] and not game['first_move']:
		no_moves = not player_has_moves(game, slot)
		if no_moves:
			max_players_local = game.get('max_players', 2)
			others_stuck = all(not player_has_moves(game, i) for i in range(max_players_local) if i != slot)
			if others_stuck:
				for i in range(max_players_local):
					game['scores'][i] -= rack_value(game['racks'][i])
				finalize_game(room, 'no_moves')
				save_room(room.get('_rid'), room)
				notify_room(room.get('_rid'))
	user_ids = room.get('user_ids') or []
	uid = user_ids[slot] if 0 <= slot < len(user_ids) else None
	ai_thinking = bool(game.get('vs_computer') and not game['game_over']
					   and game['current'] == get_ai_slot(game)
					   and time.time() < game.get('ai_play_after', 0))
	return jsonify({
		'board': game['board'],
		'blanks': game['blanks'],
		'tile_owners': game['tile_owners'],
		'bonus': game['bonus_board'],
		'mode': game.get('mode', 'classic'),
		'rack': [] if is_spectator else game['racks'][slot],
		'scores': game['scores'],
		'moves': game['moves'],
		'bluffs': game.get('bluffs', [0] * game.get('max_players', 2)),
		'current': game['current'],
		'my_slot': None if is_spectator else slot,
		'is_spectator': is_spectator,
		'has_opponent': len(filled_slots(room)) >= 2,
		'is_full': len(filled_slots(room)) >= game.get('max_players', 2),
		'max_players': game.get('max_players', 2),
		'player_count': len(filled_slots(room)),
		'names': room['names'],
		'user_ids': user_ids,
		'colors': room['colors'],
		'chat': room['chat'],
		'chat_seq': room['chat_seq'],
		'speed_mode': bool(game.get('speed_mode')),
		'challenge_mode': bool(game.get('challenge_mode')),
		'transparent_mode': bool(game.get('transparent_mode')),
		'pending_views': game.get('pending_views', {}),
		'can_challenge': not is_spectator and bool(game.get('challenge_mode')) and game.get('last_play_undo') is not None and slot == game['current'] and game['last_play_undo']['player'] != slot and not game['game_over'],
		'turn_deadline': game.get('turn_deadline'),
		'max_turns': game.get('max_turns', SPEED_MAX_TURNS),
		'missed_turns': game.get('missed_turns', [0, 0]),
		'rematch': room.get('rematch', [False, False]),
		'party_played_at': game.get('party_played_at'),
		'final_summary': game['final_summary'],
		'bag_count': len(game['bag']),
		'unseen_letters': unseen_counts(game, slot),
		'first_move': game['first_move'],
		'last_words': game['last_words'],
		'last_score': game['last_score'],
		'last_player': game['last_player'],
		'last_action': game['last_action'],
		'last_definitions': game['last_definitions'],
		'last_placements': game['last_placements'],
		'last_word_cells': game['last_word_cells'],
		'consecutive_passes': game['consecutive_passes'],
		'game_over': game['game_over'],
		'game_result': game['game_result'],
		'vs_computer': bool(game.get('vs_computer')),
		'ai_difficulty': game.get('ai_difficulty', 50),
		'ai_style': game.get('ai_style', 'score'),
		'ai_thinking': ai_thinking,
		'ai_play_after': game.get('ai_play_after', 0),
		'no_moves': no_moves,
		'forfeited_slots': game.get('forfeited_slots', []) or [],
		'rematch_declined_by': room.get('rematch_declined_by'),
		'history': game.get('history', []) or [],
		'hint_tokens': get_hint_tokens(uid) if uid is not None else 0,
	})


@app.route('/validate', methods=['POST'])
@rooms_locked
def validate():
	room, game, slot = get_ctx()
	if game is None:
		return jsonify({'error': 'invalid_room'}), 404
	data = request.get_json(silent=True) or {}
	placements = data.get('placements', [])
	if not placements:
		return jsonify({'words': []})

	cleaned = clean_placements(placements)
	if cleaned is None:
		return jsonify({'words': [], 'status': 'invalid'}), 400

	status, horizontal, board_after = check_placement(game, cleaned)
	if status not in (None, 'disconnected'):
		return jsonify({'words': [], 'status': status})

	words, _new_blanks, score = placement_words(game, cleaned, horizontal, board_after)
	challenge_mode = bool(game.get('challenge_mode'))
	result = []
	for w, cell_list in words:
		result.append({
			'word': w,
			'valid': True if challenge_mode else check_dictionary(w),
			'cells': [{'row': r, 'col': c} for r, c, _l, _new in cell_list],
		})
	all_valid = bool(result) and all(w['valid'] for w in result)

	return jsonify({
		'words': result,
		'score': score,
		'status': status or ('ok' if all_valid else 'invalid'),
	})


PLACEMENT_ERRORS = {
	'occupied': 'Cell already occupied',
	'dead_cell': 'Cell is blocked in this mode',
	'not_aligned': 'Tiles must be in a single row or column',
	'gap': 'Tiles must form a contiguous line',
}


@app.route('/submit', methods=['POST'])
@rooms_locked
def submit():
	room, game, slot = get_ctx()
	if game is None:
		return jsonify({'error': 'invalid_room'}), 404
	check_speed_timeout(room)
	if game['game_over']:
		return jsonify({'error': 'Game over'}), 403
	if slot != game['current']:
		return jsonify({'error': 'Not your turn'}), 403
	if len(filled_slots(room)) < game.get('max_players', 2):
		return jsonify({'error': 'Waiting for all players to join'}), 403
	data = request.get_json(silent=True) or {}
	placements = data.get('placements', [])

	if not placements:
		return jsonify({'error': 'No tiles placed'}), 400

	placements = clean_placements(placements)
	if placements is None:
		return jsonify({'error': 'Invalid tile placement'}), 400

	status, horizontal, board_after = check_placement(game, placements)
	if status in PLACEMENT_ERRORS:
		return jsonify({'error': PLACEMENT_ERRORS[status]}), 400
	if status == 'disconnected':
		return jsonify({'error': 'First word must cover a star' if game['first_move'] else 'New tiles must connect to existing tiles or a star'}), 400
	if game['first_move'] and len(placements) < 2:
		return jsonify({'error': 'First word must be at least 2 letters'}), 400

	rack = game['racks'][slot][:]
	for p in placements:
		tile_needed = '_' if p.get('blank') else p['letter']
		if tile_needed in rack:
			rack.remove(tile_needed)
		else:
			return jsonify({'error': f'Tile {tile_needed} not in rack'}), 400

	words, _new_blanks, _score = placement_words(game, placements, horizontal, board_after)
	if not words:
		return jsonify({'error': 'Must form at least one word of 2+ letters'}), 400
	if not game.get('challenge_mode'):
		for w, _ in words:
			if not check_dictionary(w):
				return jsonify({'error': f'"{w}" is not a valid word'}), 400

	score, word_list = apply_play(room, slot, placements)
	game.get('pending_views', {}).pop(str(slot), None)
	after_action_speed(room, slot)
	touch_room(room)
	save_room(room.get('_rid'), room)
	notify_room(room.get('_rid'))
	return jsonify({
		'ok': True,
		'score': score,
		'words': word_list,
		'definitions': game['last_definitions'],
	})


@app.route('/hint', methods=['POST'])
@rooms_locked
def hint():
	room, game, slot = get_ctx()
	if game is None:
		return jsonify({'error': 'invalid_room'}), 404
	if game['game_over']:
		return jsonify({'error': 'Game over'}), 403
	if slot == SPECTATOR_SLOT:
		return jsonify({'error': 'Spectators cannot use hints'}), 403
	if slot != game['current']:
		return jsonify({'error': 'Not your turn'}), 403
	user_ids = room.get('user_ids') or []
	uid = user_ids[slot] if slot < len(user_ids) else None
	if uid is None:
		return jsonify({'error': 'You must be signed in to use hints'}), 403
	# Check and consume a token atomically
	with DB_LOCK:
		with db_connect() as c:
			row = c.execute('SELECT hint_tokens FROM user_stats WHERE user_id=?', (uid,)).fetchone()
			tokens = (row['hint_tokens'] if row else 0) or 0
			if tokens < 1:
				return jsonify({'error': 'No hint tokens available'}), 400
			c.execute('UPDATE user_stats SET hint_tokens = hint_tokens - 1 WHERE user_id=?', (uid,))
			c.commit()
	rack = game['racks'][slot]
	moves = move_gen.generate_moves(
		game['board'], rack, game['bonus_board'], game['blanks'],
		WORD_TRIE, DICTIONARY, first_move=game['first_move'],
	)
	if not moves:
		# Refund the token — nothing to hint
		with DB_LOCK:
			with db_connect() as c:
				c.execute('UPDATE user_stats SET hint_tokens = MIN(?, hint_tokens + 1) WHERE user_id=?',
						  (HINT_TOKEN_CAP, uid))
				c.commit()
		return jsonify({'error': 'No possible plays with your rack — token refunded', 'refunded': True}), 200
	best = max(moves, key=lambda m: m['score'])
	cells = [{'row': p['row'], 'col': p['col']} for p in best['placements']]
	return jsonify({'cells': cells, 'tokens_remaining': max(0, tokens - 1)})


@app.route('/pass', methods=['POST'])
@rooms_locked
def pass_turn():
	room, game, slot = get_ctx()
	if game is None:
		return jsonify({'error': 'invalid_room'}), 404
	check_speed_timeout(room)
	if game['game_over']:
		return jsonify({'error': 'Game over'}), 403
	if slot != game['current']:
		return jsonify({'error': 'Not your turn'}), 403
	if len(filled_slots(room)) < game.get('max_players', 2):
		return jsonify({'error': 'Waiting for all players to join'}), 403
	acted = game['current']
	game['consecutive_passes'] += 1
	game['moves'][acted] += 1
	set_last_action(game, acted, 'pass')
	end_turn(room, acted)
	record_history(room, acted, 'pass')
	after_action_speed(room, acted)
	touch_room(room)
	save_room(room.get('_rid'), room)
	notify_room(room.get('_rid'))
	return jsonify({'ok': True})


@app.route('/swap', methods=['POST'])
@rooms_locked
def swap():
	room, game, slot = get_ctx()
	if game is None:
		return jsonify({'error': 'invalid_room'}), 404
	check_speed_timeout(room)
	if game['game_over']:
		return jsonify({'error': 'Game over'}), 403
	if slot != game['current']:
		return jsonify({'error': 'Not your turn'}), 403
	if len(filled_slots(room)) < game.get('max_players', 2):
		return jsonify({'error': 'Waiting for all players to join'}), 403
	if len(game['bag']) < 7:
		return jsonify({'error': 'Cannot swap: fewer than 7 tiles left in bag'}), 400
	data = request.get_json(silent=True) or {}
	tiles = data.get('tiles', [])
	if not isinstance(tiles, list) or len(tiles) > 7 or not all(isinstance(t, str) for t in tiles):
		return jsonify({'error': 'Invalid tiles'}), 400
	if not tiles:
		return jsonify({'error': 'Select tiles to swap'}), 400
	if len(game['bag']) < len(tiles):
		return jsonify({'error': 'Not enough tiles in bag to swap'}), 400
	new_rack = game['racks'][game['current']][:]
	for letter in tiles:
		if letter in new_rack:
			new_rack.remove(letter)
		else:
			return jsonify({'error': f'Tile {letter} not in rack'}), 400
	for _ in tiles:
		new_rack.append(game['bag'].pop())
	game['bag'].extend(tiles)
	random.shuffle(game['bag'])
	game['racks'][game['current']] = new_rack
	acted = game['current']
	game['moves'][acted] += 1
	game['consecutive_passes'] = 0
	set_last_action(game, acted, 'swap')
	end_turn(room, acted)
	record_history(room, acted, 'swap')
	after_action_speed(room, acted)
	touch_room(room)
	save_room(room.get('_rid'), room)
	notify_room(room.get('_rid'))
	return jsonify({'ok': True})


@app.route('/challenge', methods=['POST'])
@rooms_locked
def challenge():
	room, game, slot = get_ctx()
	if game is None:
		return jsonify({'error': 'invalid_room'}), 404
	if game['game_over']:
		return jsonify({'error': 'Game over'}), 403
	if not game.get('challenge_mode'):
		return jsonify({'error': 'Challenge mode is not enabled for this room'}), 400
	undo = game.get('last_play_undo')
	if not undo or game.get('last_action') != 'play':
		return jsonify({'error': 'No play to challenge'}), 400
	if slot != game['current']:
		return jsonify({'error': 'Only the next player can challenge'}), 403
	if slot == undo['player']:
		return jsonify({'error': "Can't challenge your own play"}), 400

	invalid = [w for w in game['last_words'] if not check_dictionary(w)]
	played_by = undo['player']

	if invalid:
		for p in undo['placed']:
			r, c = p['row'], p['col']
			game['board'][r][c] = ''
			game['blanks'][r][c] = False
			game['tile_owners'][r][c] = None
		rack = game['racks'][played_by]
		for letter in undo['drawn']:
			if letter in rack:
				rack.remove(letter)
			game['bag'].append(letter)
		random.shuffle(game['bag'])
		for p in undo['placed']:
			rack.append(p['rack_letter'])
		game['scores'][played_by] -= undo['score_delta']
		game['first_move'] = undo['was_first_move']
		game['moves'][played_by] = max(0, game['moves'][played_by] - 1)
		game.setdefault('bluffs', [0] * game.get('max_players', 2))
		game.setdefault('bluff_points', [0] * game.get('max_players', 2))
		game['bluffs'][played_by] = max(0, game['bluffs'][played_by] - len(invalid))
		game['bluff_points'][played_by] = max(0, game['bluff_points'][played_by] - undo.get('bluff_points_delta', 0))
		game['last_action'] = 'challenge_won'
		game['last_words'] = list(invalid)
		game['last_score'] = 0
		game['last_player'] = slot
		game['last_definitions'] = []
		game['last_placements'] = []
		game['last_word_cells'] = []
		game['last_play_undo'] = None
		result = 'won'
	else:
		advance_turn(game)
		game['last_action'] = 'challenge_lost'
		survived_words = list(game['last_words'])
		game['last_score'] = 0
		game['last_player'] = slot
		game['last_definitions'] = [
			{'word': w, 'entry': fetch_definition(w)} for w in survived_words
		]
		game['last_words'] = survived_words
		game['last_play_undo'] = None
		result = 'lost'

	after_action_speed(room, slot)
	touch_room(room)
	save_room(room.get('_rid'), room)
	notify_room(room.get('_rid'))
	return jsonify({'ok': True, 'result': result, 'invalid_words': invalid})


@app.route('/rematch', methods=['POST'])
@rooms_locked
def rematch():
	room, game, slot = get_ctx()
	if room is None:
		return jsonify({'error': 'invalid_room'}), 404
	if slot == SPECTATOR_SLOT:
		return jsonify({'error': 'spectator'}), 403
	if not game['game_over']:
		return jsonify({'error': 'Game still in progress'}), 400
	if game.get('forfeited_slots'):
		return jsonify({'error': 'Rematch unavailable, a player forfeited'}), 400
	max_players = room.get('max_players', 2)
	data = request.get_json(silent=True) or {}
	decline = bool(data.get('decline'))
	room.setdefault('rematch', [False] * max_players)
	if len(room['rematch']) != max_players:
		room['rematch'] = [False] * max_players
	if decline:
		room['rematch'] = [False] * max_players
		room['rematch_declined_by'] = slot
		touch_room(room)
		save_room(room.get('_rid'), room)
		notify_room(room.get('_rid'))
		return jsonify({'ok': True, 'rematch': room['rematch'], 'declined': True})
	room.pop('rematch_declined_by', None)
	room['rematch'][slot] = True
	if room.get('vs_computer'):
		# AI is always ready for a rematch
		ai_slot_idx = get_ai_slot(room.get('game'))
		if ai_slot_idx is not None and ai_slot_idx < len(room['rematch']):
			room['rematch'][ai_slot_idx] = True
	if all(room['rematch']):
		mode = room.get('mode', 'classic')
		speed = room.get('speed', False)
		challenge_mode = room.get('challenge_mode', False)
		transparent_mode = room.get('transparent_mode', False)
		vs_computer = room.get('vs_computer', False)
		ai_difficulty = room.get('ai_difficulty', 50)
		ai_style = room.get('ai_style', 'score')
		room['game'] = new_game(mode=mode, speed=speed, max_players=max_players, challenge_mode=challenge_mode,
								 transparent_mode=transparent_mode, vs_computer=vs_computer, ai_difficulty=ai_difficulty,
								 ai_style=ai_style)
		room['game']['started_at'] = time.time()
		if speed:
			room['game']['turn_deadline'] = time.time() + SPEED_TURN_SECONDS
		room['rematch'] = [False] * max_players
		room.pop('quit_user_ids', None)
	touch_room(room)
	save_room(room.get('_rid'), room)
	notify_room(room.get('_rid'))
	return jsonify({'ok': True, 'rematch': room['rematch']})


@app.route('/forfeit', methods=['POST'])
@rooms_locked
def forfeit():
	room, game, slot = get_ctx()
	if room is None:
		return jsonify({'ok': True})
	rid = room.get('_rid')
	if slot == SPECTATOR_SLOT:
		token = (request.get_json(silent=True) or {}).get('player_token') or ''
		if token in room.get('spectators', {}):
			del room['spectators'][token]
			room.get('spectator_ips', {}).pop(token, None)
			save_room(rid, room)
			notify_room(rid)
		return jsonify({'ok': True})
	if game and not game['game_over'] and not (filled_slots(room) - {slot, get_ai_slot(game)}) and not any(game['moves']):
		# Nobody else ever sat down and nothing was played, so the room never started: drop it and record nothing
		rooms.pop(rid, None)
		delete_room_persisted(rid)
		notify_room(rid)
		return jsonify({'ok': True})
	if game and not game['game_over']:
		forfeited = set(game.get('forfeited_slots', []) or [])
		max_players = game.get('max_players', 2)
		active_before = sum(1 for i in range(max_players) if i not in forfeited)
		# Return forfeiter's tiles to the bag
		rack = list(game['racks'][slot])
		if rack:
			game['bag'].extend(rack)
			random.shuffle(game['bag'])
			game['racks'][slot] = []
		# Remove their player tokens so they can't keep acting
		for token in [t for t, s in room['players'].items() if s == slot]:
			del room['players'][token]
		# Mark slot forfeited
		game.setdefault('forfeited_slots', [])
		if slot not in game['forfeited_slots']:
			game['forfeited_slots'].append(slot)
		# Drop user_id binding for this slot, remembering it so the game end still records their loss
		user_ids = room.get('user_ids') or []
		if slot < len(user_ids):
			if user_ids[slot]:
				room.setdefault('quit_user_ids', {})[str(slot)] = user_ids[slot]
			user_ids[slot] = None
			room['user_ids'] = user_ids
		# If it was their turn, advance to next active player
		if game['current'] == slot:
			advance_turn(game)
		record_history(room, slot, 'forfeit')
		if active_before <= 2:
			# After this forfeit only one active player remains — end the game
			try:
				finalize_game(room, 'forfeit', forced_winner=highest_active_other(game, slot))
			except Exception as e:
				print(f'forfeit finalize failed: {e}')
		touch_room(room)
		save_room(rid, room)
		notify_room(rid)
	else:
		token = next((t for t, s in room['players'].items() if s == slot), None)
		if token:
			del room['players'][token]
		if not [t for t in room['players'] if t != AI_TOKEN]: # the computer's seat never leaves, so it does not keep the room alive
			rooms.pop(rid, None)
			delete_room_persisted(rid)
		else:
			save_room(rid, room)
		notify_room(rid)
	return jsonify({'ok': True})


@app.route('/chat', methods=['POST'])
@rooms_locked
def post_chat():
	room, game, slot = get_ctx()
	if room is None:
		return jsonify({'error': 'invalid_room'}), 404
	data = request.get_json(silent=True) or {}
	text = (data.get('text') or '').strip()
	if not text:
		return jsonify({'error': 'Empty message'}), 400
	text = text[:CHAT_TEXT_MAX]

	now = time.time()
	is_spectator = slot == SPECTATOR_SLOT
	max_players = room['game'].get('max_players', 2)
	if is_spectator:
		token = (request.get_json(silent=True) or {}).get('player_token') or ''
		spec_limits = room.setdefault('chat_spec_limits', {})
		times = spec_limits.setdefault(token, [])
	else:
		limits = room.setdefault('chat_limits', [[] for _ in range(max_players)])
		while len(limits) < max_players:
			limits.append([])
		times = limits[slot]
	cutoff = now - CHAT_RATE_WINDOW
	ip_limits = room.setdefault('chat_ip_limits', {}) # per IP too, since new spectator tokens are free
	for key in [k for k, v in ip_limits.items() if not v or v[-1] <= cutoff]:
		del ip_limits[key]
	ip_times = ip_limits.setdefault(request.remote_addr, [])
	times[:] = [t for t in times if t > cutoff]
	ip_times[:] = [t for t in ip_times if t > cutoff]
	if len(times) >= CHAT_RATE_MAX or len(ip_times) >= CHAT_RATE_MAX:
		retry = round((min(times[:1] + ip_times[:1]) + CHAT_RATE_WINDOW) - now, 2)
		return jsonify({'error': f'Slow down — wait {retry}s', 'retry_after': retry}), 429
	times.append(now)
	ip_times.append(now)

	if is_spectator:
		token = (request.get_json(silent=True) or {}).get('player_token') or ''
		chat_name = room.get('spectators', {}).get(token, 'Watcher')
	else:
		chat_name = room['names'][slot] or f'P{slot + 1}'
	room['chat_seq'] += 1
	room['chat'].append({
		'id': room['chat_seq'],
		'slot': slot,
		'name': chat_name,
		'spectator': is_spectator,
		'text': text,
		'at': int(time.time()),
	})
	if len(room['chat']) > CHAT_MAX:
		room['chat'] = room['chat'][-CHAT_MAX:]
	touch_room(room)
	save_room(room.get('_rid'), room)
	notify_room(room.get('_rid'))
	return jsonify({'ok': True})


@app.route('/auth/captcha', methods=['GET'])
def auth_captcha():
	if arcade.enabled():
		return jsonify(arcade.captcha())
	if blocked('captcha', request.remote_addr):
		return jsonify({'error': 'Too many captchas, wait a minute'}), 429
	attempt('captcha', request.remote_addr)
	return jsonify(captcha_create())


@app.route('/auth/signup', methods=['POST'])
def auth_signup():
	data = request.get_json(silent=True) or {}
	username = (data.get('username') or '').strip()
	password = data.get('password') or ''
	captcha_id = data.get('captcha_id')
	captcha_answer = data.get('captcha_answer')
	if arcade.enabled():
		return arcade_session_reply(arcade.signup(username, password, captcha_id, captcha_answer, request.remote_addr))
	if not USERNAME_RE.match(username):
		return jsonify({'error': 'Username must be 3-16 letters/digits/underscores'}), 400
	if len(password) < 6 or len(password) > 128:
		return jsonify({'error': 'Password must be 6-128 characters'}), 400
	if not captcha_verify(captcha_id, captcha_answer):
		return jsonify({'error': 'Captcha incorrect or expired'}), 400
	pw_hash = generate_password_hash(password)
	now = int(time.time())
	try:
		with DB_LOCK:
			with db_connect() as c:
				cur = c.execute('INSERT INTO users(username, password_hash, created_at) VALUES(?, ?, ?)',
								(username, pw_hash, now))
				user_id = cur.lastrowid
				c.execute('INSERT INTO user_stats(user_id) VALUES(?)', (user_id,))
				c.commit()
	except sqlite3.IntegrityError:
		return jsonify({'error': 'Username taken'}), 409
	token = create_session(user_id)
	return jsonify({'auth_token': token, 'username': username})


@app.route('/auth/login', methods=['POST'])
def auth_login():
	data = request.get_json(silent=True) or {}
	username = (data.get('username') or '').strip()
	password = data.get('password') or ''
	if not username or not password:
		return jsonify({'error': 'Username and password required'}), 400
	if arcade.enabled():
		return arcade_session_reply(arcade.login(username, password, request.remote_addr))
	if blocked('account', username.lower()) or blocked('ip', request.remote_addr):
		return jsonify({'error': 'Too many failed logins, try again in 15 minutes'}), 429
	with DB_LOCK:
		with db_connect() as c:
			row = c.execute('SELECT id, password_hash FROM users WHERE username=?', (username,)).fetchone()
	if not row or not check_password_hash(row['password_hash'], password):
		attempt('account', username.lower())
		attempt('ip', request.remote_addr)
		return jsonify({'error': 'Invalid username or password'}), 401
	token = create_session(row['id'])
	return jsonify({'auth_token': token, 'username': username})


@app.route('/auth/logout', methods=['POST'])
def auth_logout():
	data = request.get_json(silent=True) or {}
	token = data.get('auth_token')
	if arcade.enabled():
		if token:
			arcade.logout(token)
		resp = jsonify({'ok': True})
		arcade.clear_cookie(resp, request.host)
		return resp
	if token:
		with DB_LOCK:
			with db_connect() as c:
				c.execute('DELETE FROM sessions WHERE token=?', (token,))
				c.commit()
	return jsonify({'ok': True})


@app.route('/auth/me', methods=['GET'])
def auth_me():
	uid = get_request_user_id()
	if uid is None:
		return jsonify({'user': None})
	name = get_username(uid)
	resp = jsonify({'user': {'username': name, 'user_id': uid}})
	if arcade.enabled() and not request.cookies.get(arcade.COOKIE):
		arcade.set_cookie(resp, request.host, request.headers.get('X-Auth-Token'))
	return resp


@app.route('/auth/sso', methods=['GET'])
def auth_sso():
	'''Swap the shared sign-in cookie set by any acid-arcade site on this domain for a session here.'''

	if not arcade.enabled():
		return 'Not found', 404
	d = arcade.sso(request.cookies.get(arcade.COOKIE))
	if not d:
		resp = jsonify({'error': 'not signed in'})
		arcade.clear_cookie(resp, request.host)
		return resp, 401
	return arcade_session_reply(d)


@app.route('/auth/stats', methods=['GET'])
def auth_stats():
	uname = (request.args.get('username') or '').strip()
	if uname:
		with db_connect() as c:
			urow = c.execute('SELECT id FROM users WHERE username=?', (uname,)).fetchone()
		if not urow:
			return jsonify({'error': 'User not found'}), 404
		uid = urow['id']
	else:
		uid = get_request_user_id()
		if uid is None:
			return jsonify({'error': 'Not authenticated'}), 401
		uname = get_username(uid)
	with db_connect() as c:
		srow = c.execute('SELECT * FROM user_stats WHERE user_id=?', (uid,)).fetchone()
		top_words = c.execute('SELECT word, count FROM user_word_counts WHERE user_id=? ORDER BY count DESC, word ASC LIMIT 10', (uid,)).fetchall()
	if not srow:
		srow = {}
	def gv(k, d=0):
		try: return srow[k]
		except Exception: return d
	return jsonify({
		'username': uname,
		'user_id': uid,
		'games_played': gv('games_played'),
		'wins': gv('wins'),
		'losses': gv('losses'),
		'draws': gv('draws'),
		'highest_play_score': gv('highest_play_score'),
		'highest_play_words': json.loads(srow['highest_play_words']) if srow and srow['highest_play_words'] else [],
		'longest_word': gv('longest_word', ''),
		'most_words_in_play': gv('most_words_in_play'),
		'most_words_play_words': json.loads(srow['most_words_play_words']) if srow and srow['most_words_play_words'] else [],
		'total_play_score': gv('total_play_score'),
		'total_plays': gv('total_plays'),
		'current_win_streak': gv('current_win_streak'),
		'longest_win_streak': gv('longest_win_streak'),
		'hint_tokens': gv('hint_tokens'),
		'wins_to_next_token': max(0, WINS_PER_TOKEN - gv('wins_since_last_token')),
		'hint_token_cap': HINT_TOKEN_CAP,
		'top_words': [{'word': r['word'], 'count': r['count']} for r in top_words],
	})


@app.route('/auth/active_rooms', methods=['GET'])
@rooms_locked
def auth_active_rooms():
	uid = get_request_user_id()
	if uid is None:
		return jsonify({'rooms': []})
	out = []
	for rid, room in rooms.items():
		if room['game'].get('game_over'):
			continue
		uids = room.get('user_ids') or []
		if uid in uids:
			out.append({'room_id': rid, 'slot': uids.index(uid)})
	return jsonify({'rooms': out})


@app.route('/leaderboard')
def leaderboard():
	try:
		limit = max(1, min(50, int(request.args.get('limit', 10))))
	except ValueError:
		limit = 10
	with db_connect() as c:
		rows_classic = c.execute('SELECT word, count FROM word_counts_v2 WHERE mode=? ORDER BY count DESC, word ASC LIMIT ?', ('classic', limit)).fetchall()
		rows_random = c.execute('SELECT word, count FROM word_counts_v2 WHERE mode=? ORDER BY count DESC, word ASC LIMIT ?', ('random', limit)).fetchall()
	def mode_block(mode, rows):
		return {
			'total_games': meta_get(f'total_games_{mode}', 0) or 0,
			'top_play': current_names(meta_get(f'top_play_{mode}')),
			'top_game': current_names(meta_get(f'top_game_{mode}')),
			'longest_word': current_names(meta_get(f'longest_word_{mode}')),
			'top_words': [{'word': r['word'], 'count': r['count']} for r in rows],
		}
	return jsonify({
		'classic': mode_block('classic', rows_classic),
		'random': mode_block('random', rows_random),
	})


@app.route('/online')
def online():
	with SUBSCRIBERS_LOCK:
		total = sum(len(qs) for qs in SUBSCRIBERS.values())
		rooms_active = sum(1 for qs in SUBSCRIBERS.values() if qs)
	return jsonify({'players': total, 'rooms': rooms_active})


if __name__ == '__main__':
	app.run(host='0.0.0.0', port=1337, debug=False, threaded=True)
