#!/usr/bin/env python3
# scrabble - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_app.py

import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

os.environ['SCRABBLE_DATA_DIR'] = tempfile.mkdtemp(prefix='scrabble_test_')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scrabble'))

import app as server
import arcade
import auth
import game
import move_gen
import storage
import words


def tearDownModule():
	'''Remove the throwaway data directory.'''

	server.rooms.clear() # nothing left for the exit hook to save into the removed directory
	shutil.rmtree(os.environ['SCRABBLE_DATA_DIR'], ignore_errors=True)


def wait_called(fn: mock.Mock, timeout: float = 5):
	'''Wait for a mock to be called from a background thread.'''

	end = time.time() + timeout
	while not fn.called and time.time() < end:
		time.sleep(0.02)


class TestApp(unittest.TestCase):
	'''HTTP API tests against an in-process app with a throwaway database.'''

	def setUp(self):
		'''Create a test client.'''

		self.client = server.app.test_client()
		auth.ATTEMPTS.clear()


	def call(self, path: str, body: dict = None, auth_token: str = '') -> tuple:
		'''Make a GET or POST request and return (JSON reply, status).'''

		headers = {'X-Auth-Token': auth_token}
		resp    = self.client.get(path, headers=headers) if body is None else self.client.post(path, json=body, headers=headers)
		return resp.get_json(), resp.status_code


	def test_move_search_budget(self):
		'''A full move search stops at SEARCH_SECONDS with the moves found so far, and the quick "any move?" check is not cut short.'''

		room = game.new_room()
		g    = room['game']
		for i, ch in enumerate('RETAINS'):
			g['board'][7][4 + i] = ch
		args = (g['board'], list('__ESRAT'), g['bonus_board'], g['blanks'], words.WORD_TRIE, words.DICTIONARY)
		with mock.patch.object(move_gen, 'SEARCH_SECONDS', 0.2):
			start = time.monotonic()
			moves = move_gen.generate_moves(*args)
			self.assertLess(time.monotonic() - start, 0.5)
			self.assertTrue(moves)
			self.assertTrue(move_gen.has_any_move(*args))


	def test_spectator_chat_and_pending_caps(self):
		'''Watchers per room and per IP are capped, chat is limited per IP across tokens, and pending tiles are capped at 7 squares.'''

		a, _ = self.call('/create_room', {'name': 'X', 'transparent_mode': True})
		rid  = a['room_id']
		self.call('/join_room', {'room_id': rid, 'name': 'Y'})
		flood = [self.client.post('/watch_room', json={'room_id': rid}, environ_base={'REMOTE_ADDR': '10.5.0.1'}).get_json()['player_token'] for _ in range(15)]
		for i in range(60):
			self.client.post('/watch_room', json={'room_id': rid}, environ_base={'REMOTE_ADDR': f'10.6.0.{i}'})
		room = server.rooms[rid]
		self.assertEqual(len(room['spectators']), server.MAX_SPECTATORS)
		self.assertNotIn(flood[0], room['spectators'])
		tokens = [self.client.post('/watch_room', json={'room_id': rid}, environ_base={'REMOTE_ADDR': '10.5.0.2'}).get_json()['player_token'] for _ in range(3)]
		codes  = [self.client.post('/chat', json={'room_id': rid, 'player_token': t, 'text': 'hi'}, environ_base={'REMOTE_ADDR': '10.5.0.2'}).status_code for t in tokens]
		self.assertEqual(codes, [200, 200, 429])
		self.assertEqual(self.call('/share_pending', {'room_id': rid, 'player_token': a['player_token'], 'placements': [{'row': 7, 'col': c % 15, 'letter': 'A'} for c in range(50)] + ['junk']})[1], 200)
		self.assertEqual(len(room['game']['pending_views']['0']), 7)
		self.assertEqual(self.client.post('/chat', data='x' * 70_000, content_type='application/json').status_code, 413)


	def test_bluffs_skip_records(self):
		'''A challenge mode play with an invalid word scores in the game but is not sent to the records.'''

		for word, counted in (('ZQX', False), ('CAT', True)):
			room = game.new_room(challenge_mode=True)
			room['game']['racks'][0] = list(word) + ['E'] * (7 - len(word))
			with mock.patch.object(game, 'record_play_metrics') as metrics:
				score, _ = game.apply_play(room, 0, [{'row': 7, 'col': 6 + i, 'letter': ch} for i, ch in enumerate(word)])
			self.assertGreater(score, 0)
			self.assertEqual(metrics.called, counted, word)


	def test_room_limit_per_ip(self):
		'''One IP may have 5 open rooms, a 6th is refused, another IP still works, and a finished room frees a spot.'''

		ip    = {'REMOTE_ADDR': '198.51.100.1'}
		made  = [self.client.post('/create_room', json={'name': 'X'}, environ_base=ip) for _ in range(5)]
		self.assertTrue(all(r.status_code == 200 for r in made))
		resp  = self.client.post('/create_room', json={'name': 'X'}, environ_base=ip)
		self.assertEqual((resp.status_code, resp.get_json()), (429, {'error': 'you already have 5 open rooms, finish or leave one first'}))
		self.assertEqual(self.client.post('/create_room', json={'name': 'X'}, environ_base={'REMOTE_ADDR': '198.51.100.2'}).status_code, 200)
		self.assertEqual(self.client.post('/join_room', json={'room_id': made[0].get_json()['room_id'], 'name': 'Y'}, environ_base=ip).status_code, 200)
		server.rooms[made[0].get_json()['room_id']]['game']['game_over'] = True
		self.assertEqual(self.client.post('/create_room', json={'name': 'X'}, environ_base=ip).status_code, 200)


	def persisted(self, rid: str) -> bool:
		'''Return whether a room is still saved in rooms.sqlite.'''

		with sqlite3.connect(storage.ROOMS_DB_PATH) as c:
			return c.execute('SELECT 1 FROM rooms WHERE id=?', (rid,)).fetchone() is not None


	def test_leave_unstarted_room(self):
		'''Leaving a room nobody else joined and nobody played in deletes it and records nothing, for a lobby and a computer game alike.'''

		for body in ({'name': 'X'}, {'name': 'X', 'vs_computer': True}):
			a, _ = self.call('/create_room', body)
			with mock.patch.object(game, 'record_game_end') as record:
				self.assertEqual(self.call('/forfeit', {'room_id': a['room_id'], 'player_token': a['player_token']}), ({'ok': True}, 200))
			record.assert_not_called()
			self.assertNotIn(a['room_id'], server.rooms)
			self.assertFalse(self.persisted(a['room_id']))
		a, _ = self.call('/create_room', {'name': 'X'})
		self.call('/join_room', {'room_id': a['room_id'], 'name': 'Y'})
		self.call('/forfeit', {'room_id': a['room_id'], 'player_token': a['player_token']})
		self.assertTrue(server.rooms[a['room_id']]['game']['game_over'])


	def test_leave_finished_computer_room(self):
		'''Leaving a finished game against the computer deletes the room, since the computer's seat does not count as a player.'''

		a, _ = self.call('/create_room', {'name': 'X', 'vs_computer': True})
		rid  = a['room_id']
		game.finalize_game(server.rooms[rid], 'no_moves')
		self.call('/forfeit', {'room_id': rid, 'player_token': a['player_token']})
		self.assertNotIn(rid, server.rooms)
		self.assertFalse(self.persisted(rid))


	def test_rematch_after_forfeit(self):
		'''Once anyone forfeits, /rematch is refused and the state shows the forfeit so the page hides the button.'''

		a, _ = self.call('/create_room', {'name': 'X'})
		b, _ = self.call('/join_room', {'room_id': a['room_id'], 'name': 'Y'})
		self.call('/forfeit', {'room_id': b['room_id'], 'player_token': b['player_token']})
		self.assertEqual(self.call('/rematch', {'room_id': a['room_id'], 'player_token': a['player_token']}), ({'error': 'Rematch unavailable, a player forfeited'}, 400))
		s, _ = self.call(f'/state?room_id={a["room_id"]}&player_token={a["player_token"]}')
		self.assertEqual((s['game_over'], s['forfeited_slots'], s['rematch']), (True, [1], [False, False]))


	def test_swap_validation(self):
		'''/swap refuses tiles that are not a list of at most 7 strings with a 400 instead of failing.'''

		a, _ = self.call('/create_room', {'name': 'X'})
		self.call('/join_room', {'room_id': a['room_id'], 'name': 'Y'})
		for tiles in (5, 'AB', {'A': 1}, [1], ['A'] * 8):
			self.assertEqual(self.call('/swap', {'room_id': a['room_id'], 'player_token': a['player_token'], 'tiles': tiles}), ({'error': 'Invalid tiles'}, 400), tiles)
		rack = server.rooms[a['room_id']]['game']['racks'][0]
		self.assertEqual(self.call('/swap', {'room_id': a['room_id'], 'player_token': a['player_token'], 'tiles': rack[:2]}), ({'ok': True}, 200))


	def test_seo_urls(self):
		'''Canonical, og:url, JSON-LD, robots and sitemap use SCRABBLE_PUBLIC_URL when set and the request host when not.'''

		for public, expect in (('', 'https://scrabble.supernets.org/'), ('https://scrabble.acid.vegas', 'https://scrabble.acid.vegas/')):
			with mock.patch.object(server, 'PUBLIC_URL', public):
				html = self.client.get('/', base_url='https://scrabble.supernets.org').get_data(as_text=True)
				self.assertIn(f'<link rel="canonical" href="{expect}">', html)
				self.assertIn(f'<meta property="og:url" content="{expect}">', html)
				self.assertIn(f'<meta property="og:image" content="{expect}static/og.png">', html)
				data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html).group(1))
				self.assertEqual((data['url'], data['image'], data['@type']), (expect, f'{expect}static/og.png', ['VideoGame', 'WebApplication']))
				self.assertIn(f'Sitemap: {expect}sitemap.xml', self.client.get('/robots.txt', base_url='https://scrabble.supernets.org').get_data(as_text=True))
				self.assertIn(f'<loc>{expect}</loc>', self.client.get('/sitemap.xml', base_url='https://scrabble.supernets.org').get_data(as_text=True))
		room = self.client.get('/abcd').get_data(as_text=True)
		self.assertIn('<meta name="robots" content="noindex">', room)
		self.assertNotIn('rel="canonical"', room)


	def test_standalone_auth(self):
		'''Local signup, login, me and logout work without acid-arcade-db.'''

		self.assertFalse(arcade.enabled())
		cap, _ = self.call('/auth/captcha')
		data, status = self.call('/auth/signup', {'username': 'alice', 'password': 'hunter22x', 'captcha_id': cap['captcha_id'], 'captcha_answer': 'WRONG'})
		self.assertEqual((data['error'], status), ('Captcha incorrect or expired', 400))
		cap, _ = self.call('/auth/captcha')
		text   = auth.CAPTCHAS[cap['captcha_id']][0]
		data, status = self.call('/auth/signup', {'username': 'alice', 'password': 'hunter22x', 'captcha_id': cap['captcha_id'], 'captcha_answer': text.lower()})
		self.assertEqual((status, data['username']), (200, 'alice'))
		self.assertEqual(self.call('/auth/me', auth_token=data['auth_token'])[0]['user']['username'], 'alice')
		self.assertEqual(self.call('/auth/stats', auth_token=data['auth_token'])[0]['games_played'], 0)
		cap, _ = self.call('/auth/captcha')
		text   = auth.CAPTCHAS[cap['captcha_id']][0]
		self.assertEqual(self.call('/auth/signup', {'username': 'alice', 'password': 'hunter22x', 'captcha_id': cap['captcha_id'], 'captcha_answer': text})[1], 409)
		self.assertEqual(self.call('/auth/login', {'username': 'alice', 'password': 'nope'})[1], 401)
		data, status = self.call('/auth/login', {'username': 'alice', 'password': 'hunter22x'})
		self.assertEqual(status, 200)
		self.call('/auth/logout', {'auth_token': data['auth_token']})
		self.assertIsNone(self.call('/auth/me', auth_token=data['auth_token'])[0]['user'])
		self.assertIn('data-arcade-public-url=""', self.client.get('/').get_data(as_text=True))


	def test_standalone_captcha_limits(self):
		'''The captcha SVG has no text, the store keeps at most CAPTCHA_MAX, and the 31st captcha a minute from one IP is refused.'''

		self.assertTrue(set(auth.CAPTCHA_CHARS) <= set(auth.GLYPHS))
		self.assertNotIn('<text', self.call('/auth/captcha')[0]['svg'])
		with mock.patch.object(auth, 'CAPTCHA_MAX', 3):
			ids = [auth.captcha_create()['captcha_id'] for _ in range(5)]
			self.assertEqual(list(auth.CAPTCHAS), ids[2:])
		ip    = {'REMOTE_ADDR': '198.51.100.6'}
		codes = [self.client.get('/auth/captcha', environ_base=ip).status_code for _ in range(31)]
		self.assertEqual((codes[:30].count(200), codes[30]), (30, 429))
		self.assertEqual(self.client.get('/auth/captcha', environ_base={'REMOTE_ADDR': '198.51.100.7'}).status_code, 200)


	def test_standalone_login_limits(self):
		'''The 11th failed login for one account is refused even with the right password, and another account still signs in.'''

		for name in ('erin', 'frank'):
			cap  = self.call('/auth/captcha')[0]
			text = auth.CAPTCHAS[cap['captcha_id']][0]
			self.assertEqual(self.call('/auth/signup', {'username': name, 'password': 'hunter22x', 'captcha_id': cap['captcha_id'], 'captcha_answer': text})[1], 200)
		codes = [self.call('/auth/login', {'username': 'Erin' if i % 2 else 'erin', 'password': 'nope'})[1] for i in range(10)]
		self.assertEqual(codes, [401] * 10)
		self.assertEqual(self.call('/auth/login', {'username': 'erin', 'password': 'hunter22x'}), ({'error': 'Too many failed logins, try again in 15 minutes'}, 429))
		self.assertEqual(self.call('/auth/login', {'username': 'frank', 'password': 'hunter22x'})[1], 200)


	def test_standalone_no_cookie(self):
		'''Standalone mode never sets the shared sign-in cookie and has no /auth/sso.'''

		cap  = self.call('/auth/captcha')[0]
		text = auth.CAPTCHAS[cap['captcha_id']][0]
		resp = self.client.post('/auth/signup', json={'username': 'dave', 'password': 'hunter22x', 'captcha_id': cap['captcha_id'], 'captcha_answer': text})
		replies = [resp, self.client.post('/auth/login', json={'username': 'dave', 'password': 'hunter22x'}), self.client.get('/auth/me', headers={'X-Auth-Token': resp.get_json()['auth_token']}), self.client.post('/auth/logout', json={'auth_token': resp.get_json()['auth_token']})]
		for r in replies:
			self.assertEqual(r.headers.getlist('Set-Cookie'), [])
		self.assertEqual(self.client.get('/auth/sso').status_code, 404)


	def test_standalone_sends_nothing(self):
		'''Standalone mode never sends events, stats, presence or activity to acid-arcade-db.'''

		with mock.patch.object(arcade, 'event') as event, mock.patch.object(arcade, 'push_stats') as push_stats, mock.patch.object(arcade, 'call') as call:
			storage.record_play_metrics(['QUIXOTIC'], 'dave', 120, 'x', 'random', user_id=1)
			room = game.new_room(vs_computer=True)
			room.update({'_rid': 'abcd', 'user_ids': [1, None]})
			room['game']['moves'] = [9, 9]
			game.finalize_game(room, 'tiles_out')
			storage.push_arcade_event(1, 'win', 'won a Scrabble game with 1 points')
			storage.push_arcade_stats(1)
			time.sleep(0.1)
			self.assertFalse(event.called or push_stats.called or call.called)
		self.assertFalse(any(getattr(t, '_target', None) is server.presence_loop for t in threading.enumerate()))


	def test_stream_limit_per_ip(self):
		'''One IP may hold 20 event streams, the 21st is refused, and closing a stream frees its slot.'''

		ip      = {'REMOTE_ADDR': '198.51.100.3'}
		a       = self.client.post('/create_room', json={'name': 'X'}, environ_base={'REMOTE_ADDR': '198.51.100.4'}).get_json()
		url     = f'/events?room_id={a["room_id"]}&player_token={a["player_token"]}'
		streams = [self.client.get(url, buffered=False, environ_base=ip) for _ in range(20)]
		self.assertTrue(all(r.status_code == 200 for r in streams))
		self.assertEqual(self.client.get(url, buffered=False, environ_base=ip).status_code, 429)
		other   = self.client.get(url, buffered=False, environ_base={'REMOTE_ADDR': '198.51.100.5'})
		self.assertEqual(other.status_code, 200)
		other.close()
		next(streams[0].response)
		streams[0].close()
		streams[1].close()
		streams = streams[2:] + [self.client.get(url, buffered=False, environ_base=ip) for _ in range(2)]
		self.assertTrue(all(r.status_code == 200 for r in streams))
		self.assertEqual(self.client.get(url, buffered=False, environ_base=ip).status_code, 429)
		for r in streams:
			r.close()
		self.assertNotIn('198.51.100.3', server.STREAM_IPS)


class TestArcade(unittest.TestCase):
	'''acid-arcade-db mode with the arcade client functions mocked out.'''

	USERS = {'tok-a': {'user_id': 1001, 'username': 'arcadeann', 'admin': False}, 'tok-b': {'user_id': 1002, 'username': 'arcadebob', 'admin': False}, 'tok-c': {'user_id': 1003, 'username': 'arcadecat', 'admin': False}, 'tok-d': {'user_id': 1004, 'username': 'arcadedan', 'admin': False}, 'tok-e': {'user_id': 1005, 'username': 'arcadeeve', 'admin': False}}

	def setUp(self):
		'''Turn arcade mode on and fake its session lookup.'''

		self.client  = server.app.test_client()
		self.patches = [mock.patch.object(arcade, 'enabled', return_value=True), mock.patch.object(arcade, 'ARCADE_PUBLIC', 'https://arcade.{domain}'), mock.patch.object(arcade, 'session', side_effect=lambda t: self.USERS.get(t)), mock.patch.object(arcade, 'push_stats'), mock.patch.object(arcade, 'event'), mock.patch.object(arcade, 'activity', return_value={'granted': 0, 'reason': None, 'dup': False})]
		for p in self.patches:
			p.start()


	def tearDown(self):
		'''Turn arcade mode back off.'''

		for p in self.patches:
			p.stop()


	def call(self, path: str, body: dict = None, auth_token: str = '') -> tuple:
		'''Make a GET or POST request and return (JSON reply, status).'''

		headers = {'X-Auth-Token': auth_token}
		resp    = self.client.get(path, headers=headers) if body is None else self.client.post(path, json=body, headers=headers)
		return resp.get_json(), resp.status_code


	def user_row(self, user_id: int):
		'''Return a mirror users row or None.'''

		with storage.db_connect() as c:
			return c.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()


	def acts(self, count: int) -> list:
		'''Wait for activity reports and return their (user_id, kind, ref, ip, data) arguments.'''

		end = time.time() + 5
		while arcade.activity.call_count < count and time.time() < end:
			time.sleep(0.02)
		time.sleep(0.05)
		self.assertEqual(arcade.activity.call_count, count)
		return [c[0] for c in arcade.activity.call_args_list]


	def test_ai_game_activity(self):
		'''A finished 1-on-1 game against the computer is reported with the player's turns and both scores, except abandoned games, guests, rooms with more seats and a forfeit by the computer.'''

		def ended(reason: str, scores: list, user_id: int = 7001, seats: int = 2, forfeited: list = None, vs_computer: bool = True) -> dict:
			room = game.new_room(max_players=seats, vs_computer=vs_computer, ai_difficulty=60)
			room.update({'_rid': 'abcd', 'creator_ip': '203.0.113.9', 'user_ids': [user_id] + [None] * (seats - 1)})
			room['game'].update({'scores': scores, 'moves': [9] + [8] * (seats - 1), 'forfeited_slots': forfeited or []})
			game.finalize_game(room, reason)
			return room

		room = ended('tiles_out', [320, 280])
		self.assertEqual(self.acts(1)[0], (7001, 'ai_game', f'abcd-{int(room["game"]["ended_at"] * 1000)}', '203.0.113.9', {'result': 'win', 'difficulty': 60, 'style': 'score', 'turns': 9, 'score': 320, 'ai_score': 280}))
		ended('three_passes', [200, 280])
		ended('no_moves', [250, 250])
		ended('forfeit', [400, 100], forfeited=[0])
		self.assertEqual([a[4]['result'] for a in self.acts(4)[1:]], ['loss', 'draw', 'loss'])
		for reason, kwargs in (('timeout', {}), ('tiles_out', {'user_id': None}), ('tiles_out', {'seats': 4, 'scores': [400, 1, 1, 1]}), ('forfeit', {'forfeited': [1]}), ('tiles_out', {'vs_computer': False})):
			ended(reason, kwargs.pop('scores', [400, 100]), **kwargs)
		self.acts(4)


	def test_ai_game_activity_room(self):
		'''Through the routes: a signed in player who quits a started computer game reports a loss from their address, quitting before any turn reports nothing.'''

		a, _ = self.call('/create_room', {'name': 'X', 'vs_computer': True, 'ai_style': 'stacker'}, 'tok-e')
		self.call('/forfeit', {'room_id': a['room_id'], 'player_token': a['player_token']})
		a, _   = self.call('/create_room', {'name': 'X', 'vs_computer': True, 'ai_difficulty': 30}, 'tok-e')
		room   = server.rooms[a['room_id']]
		self.assertEqual(self.call('/pass', {'room_id': a['room_id'], 'player_token': a['player_token']})[1], 200)
		room['game']['ai_play_after'] = 0
		s, _ = self.call(f'/state?room_id={a["room_id"]}&player_token={a["player_token"]}')
		self.assertEqual(s['current'], 0)
		self.call('/forfeit', {'room_id': a['room_id'], 'player_token': a['player_token']})
		act = self.acts(1)[0]
		self.assertEqual(act[:2] + act[3:], (1005, 'ai_game', '127.0.0.1', {'result': 'loss', 'difficulty': 30, 'style': 'score', 'turns': 1, 'score': 0, 'ai_score': room['game']['scores'][1]}))
		self.assertTrue(act[2].startswith(a['room_id'] + '-'))


	def test_auth_proxy(self):
		'''Auth routes proxy to acid-arcade-db and keep the reply shapes.'''

		with mock.patch.object(arcade, 'captcha', return_value={'captcha_id': 'c1', 'svg': '<svg/>'}):
			self.assertEqual(self.call('/auth/captcha')[0], {'captcha_id': 'c1', 'svg': '<svg/>'})
		with mock.patch.object(arcade, 'signup', return_value={'token': 'tok-a', 'user_id': 1001, 'username': 'arcadeann'}) as signup:
			data, _ = self.call('/auth/signup', {'username': 'arcadeann', 'password': 'pw123456', 'captcha_id': 'c1', 'captcha_answer': 'ABCDE'})
			signup.assert_called_once_with('arcadeann', 'pw123456', 'c1', 'ABCDE', '127.0.0.1')
			self.assertEqual((data['auth_token'], data['username']), ('tok-a', 'arcadeann'))
		with mock.patch.object(arcade, 'login', side_effect=arcade.ArcadeError('wrong username or password', 401)):
			self.assertEqual(self.call('/auth/login', {'username': 'x', 'password': 'y'}), ({'error': 'wrong username or password'}, 401))
		with mock.patch.object(arcade, 'login', return_value={'token': 'tok-a', 'user_id': 1001, 'username': 'arcadeann'}):
			self.assertEqual(self.call('/auth/login', {'username': 'arcadeann', 'password': 'pw123456'})[0]['auth_token'], 'tok-a')
		with mock.patch.object(arcade, 'logout') as logout:
			self.call('/auth/logout', {'auth_token': 'tok-a'})
			logout.assert_called_once_with('tok-a')
		with mock.patch.object(arcade, 'session', side_effect=arcade.ArcadeDown('refused')):
			self.assertEqual(self.call('/auth/me', auth_token='tok-a'), ({'error': 'account server is down'}, 503))
			self.assertEqual(self.call('/create_room', {'name': 'X'}, 'tok-a')[1], 503)
		self.assertIsNone(self.call('/auth/me', auth_token='bogus')[0]['user'])
		self.assertIn('data-arcade-public-url="https://arcade.localhost"', self.client.get('/').get_data(as_text=True))


	def test_state_hides_opponent_rack(self):
		'''A player's /state never carries the bag on its own, which would reveal the opponent's rack next to the unseen tiles.'''

		a, _ = self.call('/create_room', {'name': 'X'})
		b, _ = self.call('/join_room', {'room_id': a['room_id'], 'name': 'Y'})
		s, _ = self.call(f'/state?room_id={a["room_id"]}&player_token={a["player_token"]}')
		self.assertNotIn('bag_letters', s)
		self.assertEqual(sum(s['unseen_letters'].values()), s['bag_count'] + 7)


	def pushed(self, user_id: int) -> tuple:
		'''Wait for a stats push for a user and return its (user_id, main, stats) arguments.'''

		end = time.time() + 5
		while time.time() < end:
			calls = [c[0] for c in arcade.push_stats.call_args_list if c[0][0] == user_id]
			if calls:
				return calls[-1]
			time.sleep(0.02)
		self.fail(f'no stats push for {user_id}')


	def take_turns(self, *players: dict):
		'''Have every player pass once, in turn order.'''

		slots = {self.call(f'/state?room_id={p["room_id"]}&player_token={p["player_token"]}')[0]['my_slot']: p for p in players}
		for _ in players:
			s, _ = self.call(f'/state?room_id={players[0]["room_id"]}&player_token={players[0]["player_token"]}')
			current = slots[s['current']]
			self.assertEqual(self.call('/pass', {'room_id': current['room_id'], 'player_token': current['player_token']})[1], 200)


	def test_forfeit_results(self):
		'''Forfeiting before everyone took a turn records nothing, and after that the player who quits gets the loss.'''

		a, _ = self.call('/create_room', {'name': 'X'}, 'tok-c')
		b, _ = self.call('/join_room', {'room_id': a['room_id'], 'name': 'Y'}, 'tok-d')
		self.call('/forfeit', {'room_id': b['room_id'], 'player_token': b['player_token']})
		time.sleep(0.2)
		arcade.push_stats.assert_not_called()
		a, _ = self.call('/create_room', {'name': 'X'}, 'tok-c')
		b, _ = self.call('/join_room', {'room_id': a['room_id'], 'name': 'Y'}, 'tok-d')
		self.take_turns(a, b)
		self.call('/forfeit', {'room_id': a['room_id'], 'player_token': a['player_token']})
		self.assertEqual((self.pushed(1003)[2]['Wins'], self.pushed(1003)[2]['Losses']), (0, 1))
		self.assertEqual((self.pushed(1004)[2]['Wins'], self.pushed(1004)[2]['Losses']), (1, 0))


	def test_game_end_push_and_sync(self):
		'''Mirror rows appear on first use, game end pushes stats, and renames and deletes are applied.'''

		a, _ = self.call('/create_room', {'name': 'X'}, 'tok-a')
		self.assertEqual(self.user_row(1001)['password_hash'], '')
		with storage.db_connect() as c:
			self.assertIsNotNone(c.execute('SELECT * FROM user_stats WHERE user_id=1001').fetchone())
		b, _ = self.call('/join_room', {'room_id': a['room_id'], 'name': 'Y'}, 'tok-b')
		s, _ = self.call(f'/state?room_id={a["room_id"]}&player_token={b["player_token"]}')
		self.assertEqual((s['user_ids'], s['names']), ([1001, 1002], ['arcadeann', 'arcadebob']))
		self.take_turns(a, b)
		self.call('/forfeit', {'room_id': a['room_id'], 'player_token': a['player_token']})
		pushed = self.pushed(1002)
		self.assertEqual((pushed[1], pushed[2]['Games'], pushed[2]['Wins'], pushed[2]['Losses']), (1, 1, 1, 0))
		wait_called(arcade.event)
		arcade.event.assert_called_once_with(1002, 'win', 'won a Scrabble game with 0 points')
		storage.meta_set('top_play_classic', {'words': ['QI'], 'player': 'arcadebob', 'user_id': 1002, 'score': 11, 'room_id': 'x', 'at': 0})
		with mock.patch.object(arcade, 'changes', return_value={'now': 50.0, 'changed': [{'user_id': 1002, 'username': 'arcadeann'}, {'user_id': 1001, 'username': 'annie'}], 'deleted': []}):
			self.assertEqual(auth.arcade_sync(0), 50.0)
		self.assertEqual((self.user_row(1001)['username'], self.user_row(1002)['username']), ('annie', 'arcadeann'))
		top = self.call('/leaderboard')[0]['classic']['top_play']
		self.assertEqual((top['player'], top['user_id']), ('arcadeann', 1002))
		with mock.patch.object(arcade, 'changes', return_value={'now': 60.0, 'changed': [], 'deleted': [1002]}):
			auth.arcade_sync(50.0)
		self.assertIsNone(self.user_row(1002))
		with storage.db_connect() as c:
			self.assertIsNone(c.execute('SELECT * FROM user_stats WHERE user_id=1002').fetchone())
		top = self.call('/leaderboard')[0]['classic']['top_play']
		self.assertEqual((top['player'], top['user_id']), ('arcadebob', None))


	def test_play_record_event(self):
		'''A play of 80+ points by an account posts one record event, smaller plays and guests post none.'''

		storage.record_play_metrics(['ZA'], 'arcadeann', 79, 'x', 'random', user_id=1001)
		storage.record_play_metrics(['QUIXOTIC', 'XI'], 'guest', 120, 'x', 'random')
		storage.record_play_metrics(['XI', 'QUIXOTIC'], 'arcadeann', 120, 'x', 'random', user_id=1001)
		wait_called(arcade.event)
		time.sleep(0.1)
		arcade.event.assert_called_once_with(1001, 'record', 'played QUIXOTIC for 120 points')


	def test_player_ip_and_domain(self):
		'''Login and signup forward the player's IP from X-Forwarded-For, a 429 passes through, and the arcade URL follows the request host.'''

		xff = {'X-Forwarded-For': '203.0.113.7'}
		with mock.patch.object(server.app.wsgi_app, 'x_for', 1):
			with mock.patch.object(arcade, 'login', return_value={'token': 'tok-a', 'user_id': 1001, 'username': 'arcadeann'}) as login:
				self.client.post('/auth/login', json={'username': 'arcadeann', 'password': 'pw123456'}, headers=xff)
				login.assert_called_once_with('arcadeann', 'pw123456', '203.0.113.7')
			with mock.patch.object(arcade, 'signup', return_value={'token': 'tok-a', 'user_id': 1001, 'username': 'arcadeann'}) as signup:
				self.client.post('/auth/signup', json={'username': 'arcadeann', 'password': 'pw123456', 'captcha_id': 'c1', 'captcha_answer': 'ABCDE'}, headers=xff)
				signup.assert_called_once_with('arcadeann', 'pw123456', 'c1', 'ABCDE', '203.0.113.7')
		with mock.patch.object(arcade, 'login', side_effect=arcade.ArcadeError('too many failed logins, try again later', 429)):
			self.assertEqual(self.call('/auth/login', {'username': 'x', 'password': 'y'}), ({'error': 'too many failed logins, try again later'}, 429))
		with mock.patch.object(arcade, 'signup', side_effect=arcade.ArcadeError('too many new accounts, try again later', 429)):
			self.assertEqual(self.call('/auth/signup', {'username': 'x', 'password': 'y', 'captcha_id': 'c1', 'captcha_answer': 'A'})[1], 429)
		for host, url in (('scrabble.supernets.org', 'https://arcade.supernets.org'), ('scrabble.acid.vegas', 'https://arcade.acid.vegas')):
			self.assertIn(f'data-arcade-public-url="{url}"', self.client.get('/', base_url=f'https://{host}').get_data(as_text=True))


	def test_presence(self):
		'''Presence reports the signed in players with an event stream open, and an empty list when there are none.'''

		a, _ = self.call('/create_room', {'name': 'X'}, 'tok-a')
		g, _ = self.call('/join_room', {'room_id': a['room_id'], 'name': 'GUEST'})
		with mock.patch.object(arcade, 'presence') as presence:
			server.report_presence()
			presence.assert_called_with([])
			streams = [self.client.get(f'/events?room_id={a["room_id"]}&player_token={t}', buffered=False) for t in (a['player_token'], g['player_token'])]
			server.report_presence()
			presence.assert_called_with([1001])
			for r in streams:
				next(r.response)
				r.close()
			server.report_presence()
			presence.assert_called_with([])


	def test_sso_cookies(self):
		'''Login sets the shared cookie, /auth/sso swaps it for a session, logout clears it and /auth/me shares an existing session.'''

		with mock.patch.object(arcade, 'login', return_value={'token': 'tok-a', 'user_id': 1001, 'username': 'arcadeann'}):
			resp = self.client.post('/auth/login', json={'username': 'arcadeann', 'password': 'pw123456'})
		self.assertTrue(any(c.startswith(f'{arcade.COOKIE}=tok-a;') for c in resp.headers.getlist('Set-Cookie')))
		with mock.patch.object(arcade, 'sso', side_effect=lambda t: {'token': 'tok-new', 'user_id': 1001, 'username': 'arcadeann'} if t == 'tok-a' else None) as sso:
			data = self.client.get('/auth/sso').get_json()
			self.assertEqual(data, {'auth_token': 'tok-new', 'username': 'arcadeann', 'user_id': 1001})
			sso.assert_called_with('tok-a')
			self.client.delete_cookie(arcade.COOKIE)
			resp = self.client.get('/auth/sso')
			self.assertEqual(resp.status_code, 401)
			self.assertTrue(any(c.startswith(f'{arcade.COOKIE}=;') for c in resp.headers.getlist('Set-Cookie')))
		resp = self.client.get('/auth/me', headers={'X-Auth-Token': 'tok-a'})
		self.assertEqual(resp.get_json()['user']['username'], 'arcadeann')
		self.assertTrue(any(c.startswith(f'{arcade.COOKIE}=tok-a;') for c in resp.headers.getlist('Set-Cookie')))
		resp = self.client.get('/auth/me', headers={'X-Auth-Token': 'tok-a'})
		self.assertEqual(resp.headers.getlist('Set-Cookie'), [])
		with mock.patch.object(arcade, 'logout') as logout:
			resp = self.client.post('/auth/logout', json={'auth_token': 'tok-a'})
			logout.assert_called_once_with('tok-a')
		self.assertTrue(any(c.startswith(f'{arcade.COOKIE}=;') for c in resp.headers.getlist('Set-Cookie')))
		with mock.patch.object(arcade, 'sso', side_effect=arcade.ArcadeDown('refused')):
			self.client.set_cookie(arcade.COOKIE, 'tok-a')
			self.assertEqual(self.client.get('/auth/sso').status_code, 503)



if __name__ == '__main__':
	unittest.main()
