# scrabble - Developed by acidvegas in Python (https://github.com/acidvegas)
# storage.py

import json
import os
import sqlite3
import threading
import time

import arcade


DATA_DIR = os.environ.get('SCRABBLE_DATA_DIR', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'metrics.db')
ROOMS_DB_PATH = os.path.join(DATA_DIR, 'rooms.sqlite')
DB_LOCK = threading.Lock()
ROOMS_DB_LOCK = threading.Lock()


def db_connect():
	conn = sqlite3.connect(DB_PATH)
	conn.row_factory = sqlite3.Row
	return conn


def init_db():
	with db_connect() as c:
		c.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)')
		c.execute('DROP TABLE IF EXISTS definitions')
		c.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, created_at INTEGER NOT NULL)')
		c.execute('CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, FOREIGN KEY (user_id) REFERENCES users(id))')
		c.execute('CREATE TABLE IF NOT EXISTS user_word_counts (user_id INTEGER NOT NULL, word TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (user_id, word))')
		c.execute('''CREATE TABLE IF NOT EXISTS user_stats (
			user_id INTEGER PRIMARY KEY,
			games_played INTEGER NOT NULL DEFAULT 0,
			wins INTEGER NOT NULL DEFAULT 0,
			losses INTEGER NOT NULL DEFAULT 0,
			draws INTEGER NOT NULL DEFAULT 0,
			highest_play_score INTEGER NOT NULL DEFAULT 0,
			highest_play_words TEXT,
			longest_word TEXT,
			most_words_in_play INTEGER NOT NULL DEFAULT 0,
			most_words_play_words TEXT,
			total_play_score INTEGER NOT NULL DEFAULT 0,
			total_plays INTEGER NOT NULL DEFAULT 0,
			FOREIGN KEY (user_id) REFERENCES users(id)
		)''')
		existing_cols = {r[1] for r in c.execute('PRAGMA table_info(user_stats)').fetchall()}
		for col, ddl in [
			('current_win_streak', 'INTEGER NOT NULL DEFAULT 0'),
			('longest_win_streak', 'INTEGER NOT NULL DEFAULT 0'),
			('hint_tokens', 'INTEGER NOT NULL DEFAULT 0'),
			('wins_since_last_token', 'INTEGER NOT NULL DEFAULT 0'),
		]:
			if col not in existing_cols:
				c.execute(f'ALTER TABLE user_stats ADD COLUMN {col} {ddl}')
		c.commit()


def init_rooms_db():
	with ROOMS_DB_LOCK:
		with sqlite3.connect(ROOMS_DB_PATH) as c:
			c.execute('CREATE TABLE IF NOT EXISTS rooms (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at INTEGER NOT NULL)')
			c.execute('CREATE INDEX IF NOT EXISTS rooms_updated ON rooms(updated_at)')
			c.commit()


def save_room(rid, room):
	try:
		data = json.dumps(room)
		with ROOMS_DB_LOCK:
			with sqlite3.connect(ROOMS_DB_PATH) as c:
				c.execute('INSERT OR REPLACE INTO rooms(id, data, updated_at) VALUES(?, ?, ?)',
						  (rid, data, int(time.time())))
				c.commit()
	except Exception as e:
		print(f'save_room({rid}) failed: {e}')


def delete_room_persisted(rid):
	try:
		with ROOMS_DB_LOCK:
			with sqlite3.connect(ROOMS_DB_PATH) as c:
				c.execute('DELETE FROM rooms WHERE id = ?', (rid,))
				c.commit()
	except Exception as e:
		print(f'delete_room_persisted({rid}) failed: {e}')


def load_rooms():
	init_rooms_db()
	out = {}
	try:
		with ROOMS_DB_LOCK:
			with sqlite3.connect(ROOMS_DB_PATH) as c:
				for rid, data in c.execute('SELECT id, data FROM rooms').fetchall():
					try:
						out[rid] = json.loads(data)
					except Exception as e:
						print(f'skipping corrupt room {rid}: {e}')
	except Exception as e:
		print(f'load_rooms failed: {e}')
	return out


def prune_sessions():
	with DB_LOCK:
		with db_connect() as c:
			c.execute('DELETE FROM sessions WHERE expires_at < ?', (int(time.time()),))
			c.commit()


BACKUP_DIR = os.path.join(DATA_DIR, 'backups')
BACKUP_KEEP_DAYS = 7


def _sqlite_backup(src_path, dst_path):
	src = sqlite3.connect(src_path)
	try:
		dst = sqlite3.connect(dst_path)
		try:
			with dst:
				src.backup(dst)
		finally:
			dst.close()
	finally:
		src.close()


def run_daily_backup():
	os.makedirs(BACKUP_DIR, exist_ok=True)
	today = time.strftime('%Y-%m-%d')
	targets = [
		('metrics', DB_PATH),
		('rooms', ROOMS_DB_PATH),
	]
	for name, src in targets:
		if not os.path.exists(src):
			continue
		dst = os.path.join(BACKUP_DIR, f'{name}_{today}.sqlite')
		if os.path.exists(dst):
			continue
		tmp = dst + '.tmp'
		try:
			_sqlite_backup(src, tmp)
			os.replace(tmp, dst)
			print(f'backup wrote {dst}')
		except Exception as e:
			print(f'backup of {name} failed: {e}')
			try: os.remove(tmp)
			except OSError: pass
	# Prune older than BACKUP_KEEP_DAYS
	cutoff = time.time() - BACKUP_KEEP_DAYS * 24 * 60 * 60
	try:
		for fname in os.listdir(BACKUP_DIR):
			path = os.path.join(BACKUP_DIR, fname)
			try:
				if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
					os.remove(path)
					print(f'backup pruned {fname}')
			except OSError:
				pass
	except OSError:
		pass


def backup_loop():
	# Wake up hourly; only writes a new backup if today's file isn't already there.
	while True:
		try:
			run_daily_backup()
		except Exception as e:
			print(f'backup_loop error: {e}')
		time.sleep(60 * 60)


def meta_get(key, default=None):
	with db_connect() as c:
		row = c.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
		if not row:
			return default
		try:
			return json.loads(row['value'])
		except Exception:
			return default


def meta_set(key, value):
	with db_connect() as c:
		c.execute('INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))
		c.commit()


def meta_increment(key, delta=1):
	with DB_LOCK:
		cur = meta_get(key, 0) or 0
		meta_set(key, cur + delta)


def record_play_metrics(words, player_name, score, room_id, mode='classic', user_id=None):
	# `words` should already be filtered to dictionary-valid words by the caller.
	if not words:
		return
	if mode not in ('classic', 'random'):
		mode = 'classic'
	if user_id is None:
		# Global trackers only count registered users
		return
	with DB_LOCK:
		with db_connect() as c:
			c.execute('CREATE TABLE IF NOT EXISTS word_counts_v2 (word TEXT NOT NULL, mode TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (word, mode))')
			for w in words:
				wu = w.upper()
				c.execute('INSERT INTO word_counts_v2(word, mode, count) VALUES(?, ?, 1) ON CONFLICT(word, mode) DO UPDATE SET count = count + 1', (wu, mode))
				c.execute('INSERT INTO user_word_counts(user_id, word, count) VALUES(?, ?, 1) ON CONFLICT(user_id, word) DO UPDATE SET count = count + 1', (user_id, wu))
			c.commit()
	top_key = f'top_play_{mode}'
	top_play = meta_get(top_key)
	if top_play is None or score > top_play.get('score', 0):
		meta_set(top_key, {
			'words': words,
			'player': player_name,
			'user_id': user_id,
			'score': score,
			'room_id': room_id,
			'at': int(time.time()),
		})
	long_key = f'longest_word_{mode}'
	longest = meta_get(long_key)
	longest_len = len(longest['word']) if longest else 0
	for w in words:
		if len(w) > longest_len:
			longest = {
				'word': w.upper(),
				'player': player_name,
				'user_id': user_id,
				'score': score,
				'room_id': room_id,
				'at': int(time.time()),
			}
			longest_len = len(w)
	if longest:
		meta_set(long_key, longest)
	_update_user_play_stats(user_id, words, score)
	if score >= ARCADE_RECORD_PLAY:
		push_arcade_event(user_id, 'record', f'played {max(words, key=len).upper()} for {score} points')


def _update_user_play_stats(user_id, words, score):
	if not user_id or not words:
		return
	longest_w = max(words, key=len)
	n_words = len(words)
	with DB_LOCK:
		with db_connect() as c:
			c.execute('INSERT OR IGNORE INTO user_stats(user_id) VALUES(?)', (user_id,))
			row = c.execute('SELECT highest_play_score, longest_word, most_words_in_play FROM user_stats WHERE user_id=?', (user_id,)).fetchone()
			cur_hi = (row['highest_play_score'] if row else 0) or 0
			cur_long = row['longest_word'] if row and row['longest_word'] else ''
			cur_most = (row['most_words_in_play'] if row else 0) or 0
			updates = ['total_play_score = total_play_score + ?', 'total_plays = total_plays + 1']
			params = [score]
			if score > cur_hi:
				updates.append('highest_play_score = ?')
				updates.append('highest_play_words = ?')
				params.append(score)
				params.append(json.dumps(words))
			if len(longest_w) > len(cur_long):
				updates.append('longest_word = ?')
				params.append(longest_w.upper())
			if n_words > cur_most:
				updates.append('most_words_in_play = ?')
				updates.append('most_words_play_words = ?')
				params.append(n_words)
				params.append(json.dumps(words))
			params.append(user_id)
			c.execute(f'UPDATE user_stats SET {", ".join(updates)} WHERE user_id=?', params)
			c.commit()


HINT_TOKEN_CAP = 5
ARCADE_RECORD_PLAY = 80
WINS_PER_TOKEN = 5


def get_hint_tokens(uid):
	with db_connect() as c:
		row = c.execute('SELECT hint_tokens FROM user_stats WHERE user_id=?', (uid,)).fetchone()
	return int((row['hint_tokens'] if row else 0) or 0)


def _record_user_game_end(user_id, result):
	if not user_id:
		return
	col = {'win': 'wins', 'loss': 'losses', 'draw': 'draws'}.get(result)
	if not col:
		return
	with DB_LOCK:
		with db_connect() as c:
			c.execute('INSERT OR IGNORE INTO user_stats(user_id) VALUES(?)', (user_id,))
			c.execute(f'UPDATE user_stats SET games_played = games_played + 1, {col} = {col} + 1 WHERE user_id=?', (user_id,))
			if result == 'win':
				# Streak: increment, bump longest if beaten
				c.execute('''UPDATE user_stats
							 SET current_win_streak = current_win_streak + 1,
								 longest_win_streak = MAX(longest_win_streak, current_win_streak + 1),
								 wins_since_last_token = wins_since_last_token + 1
							 WHERE user_id=?''', (user_id,))
				# Grant token if threshold reached, capped at HINT_TOKEN_CAP
				row = c.execute('SELECT wins_since_last_token, hint_tokens FROM user_stats WHERE user_id=?', (user_id,)).fetchone()
				if row and row['wins_since_last_token'] >= WINS_PER_TOKEN:
					new_tokens = min(HINT_TOKEN_CAP, (row['hint_tokens'] or 0) + 1)
					c.execute('UPDATE user_stats SET wins_since_last_token = 0, hint_tokens = ? WHERE user_id=?',
							  (new_tokens, user_id))
			elif result == 'loss':
				c.execute('UPDATE user_stats SET current_win_streak = 0 WHERE user_id=?', (user_id,))
			# draw: streak preserved
			c.commit()


def push_arcade_event(user_id: int, kind: str, text: str):
	'''Add a line to the acid-arcade-db activity feed in a background thread. Does nothing in standalone mode.'''

	if arcade.enabled():
		threading.Thread(target=send_arcade_event, args=(user_id, kind, text), daemon=True).start()


def push_arcade_stats(user_id: int):
	'''Send a player's summary to acid-arcade-db in a background thread. Does nothing in standalone mode.'''

	if not arcade.enabled():
		return
	with db_connect() as c:
		row   = c.execute('SELECT games_played, wins, losses, draws, longest_win_streak, highest_play_score FROM user_stats WHERE user_id=?', (user_id,)).fetchone()
		words = c.execute('SELECT COALESCE(SUM(count), 0) FROM user_word_counts WHERE user_id=?', (user_id,)).fetchone()[0]
	if not row:
		return
	stats = {'Games': row['games_played'], 'Wins': row['wins'], 'Losses': row['losses'], 'Draws': row['draws'], 'Best streak': row['longest_win_streak'], 'Best play': row['highest_play_score'], 'Words played': words}
	threading.Thread(target=send_arcade_stats, args=(user_id, row['wins'], stats), daemon=True).start()


def record_game_end(room, reason):
	game = room['game']
	mode = game.get('mode', 'classic')
	if mode not in ('classic', 'random'):
		mode = 'classic'
	max_players = game.get('max_players', 2)
	user_ids = room.get('user_ids', [None] * max_players)
	has_registered = any(uid for uid in user_ids[:max_players])
	if has_registered:
		meta_increment(f'total_games_{mode}')
	top_key = f'top_game_{mode}'
	# Determine per-slot game result
	summary = game.get('final_summary') or {}
	winner = summary.get('winner')
	is_draw = game.get('game_result') == 'draw' or winner is None
	for slot in range(max_players):
		s = game['scores'][slot]
		name = room['names'][slot] or f'P{slot + 1}'
		uid = user_ids[slot] if slot < len(user_ids) else None
		if uid:
			if is_draw:
				_record_user_game_end(uid, 'draw')
			elif winner == slot:
				_record_user_game_end(uid, 'win')
			else:
				_record_user_game_end(uid, 'loss')
		if not uid:
			continue
		push_arcade_stats(uid)
		if not is_draw and winner == slot:
			push_arcade_event(uid, 'win', f'won a Scrabble game with {s} points')
		others = [(room['names'][i] or f'P{i + 1}', game['scores'][i], user_ids[i] if i < len(user_ids) else None) for i in range(max_players) if i != slot]
		top_other = max(others, key=lambda x: x[1]) if others else (None, 0, None)
		top_game = meta_get(top_key)
		if top_game is None or s > top_game.get('score', 0):
			meta_set(top_key, {
				'player': name,
				'user_id': uid,
				'score': s,
				'opponent': top_other[0] or 'P?',
				'opponent_user_id': top_other[2],
				'opponent_score': top_other[1],
				'reason': reason,
				'at': int(time.time()),
			})


def send_arcade_event(user_id: int, kind: str, text: str):
	'''Send an activity feed line to acid-arcade-db, printing instead of raising on failure.'''

	try:
		arcade.event(user_id, kind, text)
	except Exception as e:
		print(f'arcade event({user_id}, {kind}) failed: {e}')


def send_arcade_stats(user_id: int, main: float, stats: dict):
	'''Push a player's summary to acid-arcade-db, printing instead of raising on failure.'''

	try:
		arcade.push_stats(user_id, main, stats)
	except Exception as e:
		print(f'arcade push_stats({user_id}) failed: {e}')
