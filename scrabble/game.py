# scrabble - Developed by acidvegas in Python (https://github.com/acidvegas)
# game.py

import random
import time

import move_gen
from boards import make_bonus_board
from storage import record_game_end, record_play_metrics
from words import DICTIONARY, WORD_TRIE, check_dictionary, fetch_definition


SPEED_TURN_SECONDS = 60
SPEED_MAX_TURNS = 5
SPEED_MAX_MISSES = 3
MIN_PLAYERS = 2
MAX_PLAYERS_CAP = 4


def touch_room(room):
	room['last_activity'] = time.time()


def check_speed_timeout(room):
	game = room['game']
	if not game.get('speed_mode'):
		return
	if game['game_over']:
		return
	max_players = game.get('max_players', 2)
	if len(filled_slots(room)) < max_players:
		return
	forfeited = set(game.get('forfeited_slots', []) or [])
	deadline = game.get('turn_deadline')
	if not deadline:
		return
	while time.time() >= deadline and not game['game_over']:
		current = game['current']
		game['moves'][current] += 1
		game['missed_turns'][current] += 1
		set_last_action(game, current, 'timeout')
		if game['missed_turns'][current] >= SPEED_MAX_MISSES:
			finalize_game(room, 'inactive', forced_winner=highest_active_other(game, current))
			break
		if all(game['moves'][i] >= game['max_turns'] for i in range(max_players) if i not in forfeited):
			finalize_game(room, 'turn_limit')
			break
		advance_turn(game)
		game['turn_deadline'] = time.time() + SPEED_TURN_SECONDS
		deadline = game['turn_deadline']


def after_action_speed(room, acted_slot):
	game = room['game']
	if not game.get('speed_mode'):
		return
	if game['game_over']:
		return
	game['missed_turns'][acted_slot] = 0
	max_players = game.get('max_players', 2)
	forfeited = set(game.get('forfeited_slots', []) or [])
	if all(game['moves'][i] >= game['max_turns'] for i in range(max_players) if i not in forfeited):
		finalize_game(room, 'turn_limit')
		return
	if len(filled_slots(room)) >= max_players:
		game['turn_deadline'] = time.time() + SPEED_TURN_SECONDS


def finalize_game(room, reason, forced_winner=None):
	game = room['game']
	if game['game_over']:
		return
	game['game_over'] = True
	game['ended_at'] = time.time()
	max_players = game.get('max_players', 2)
	bluff_bonuses = [0] * max_players
	if game.get('challenge_mode'):
		raw = game.get('bluff_points', [0] * max_players)
		for i in range(max_players):
			bluff_bonuses[i] = (raw[i] if i < len(raw) else 0) // 2
			game['scores'][i] += bluff_bonuses[i]
	if forced_winner is not None:
		winner = forced_winner
		game['game_result'] = 'win'
	else:
		forfeited = set(game.get('forfeited_slots', []) or [])
		eligible = [i for i in range(max_players) if i not in forfeited]
		if not eligible:
			eligible = list(range(max_players))
		best = max(eligible, key=lambda i: game['scores'][i])
		ties = [i for i in eligible if game['scores'][i] == game['scores'][best]]
		if len(ties) > 1:
			winner = None
			game['game_result'] = 'draw'
		else:
			winner = best
			game['game_result'] = 'win'
	duration = 0
	if game['started_at']:
		duration = int(game['ended_at'] - game['started_at'])
	game['final_summary'] = {
		'reason': reason,
		'winner': winner,
		'names': list(room['names']),
		'scores': list(game['scores']),
		'moves': list(game['moves']),
		'bluffs': list(game.get('bluffs', [0] * max_players)),
		'bluff_bonus': bluff_bonuses,
		'challenge_mode': bool(game.get('challenge_mode')),
		'started_at': int(game['started_at']) if game['started_at'] else None,
		'ended_at': int(game['ended_at']),
		'duration_seconds': duration,
	}
	try:
		record_game_end(room, reason)
	except Exception:
		pass


AI_TOKEN = '__ai__'
AI_THINK_SECONDS = 1.5


HISTORY_MAX = 250


def record_history(room, slot, action, words=None, score=0):
	game = room['game']
	history = game.setdefault('history', [])
	name = (room['names'][slot] if slot < len(room['names']) else None) or f'P{slot + 1}'
	history.append({
		'slot': slot,
		'name': name,
		'action': action,
		'words': words or [],
		'score': int(score or 0),
		'total_after': int(game['scores'][slot]) if slot < len(game['scores']) else 0,
		'ts': int(time.time()),
	})
	if len(history) > HISTORY_MAX:
		del history[:len(history) - HISTORY_MAX]


def advance_turn(game):
	"""Rotate to the next non-forfeited slot. No-op if only one active player."""
	forfeited = set(game.get('forfeited_slots', []) or [])
	max_players = game.get('max_players', 2)
	cur = game['current']
	nxt = (cur + 1) % max_players
	# Walk forward until we hit a non-forfeited slot or come back to current
	for _ in range(max_players):
		if nxt not in forfeited:
			game['current'] = nxt
			return
		nxt = (nxt + 1) % max_players
		if nxt == cur:
			break
	# All others forfeited — stay (caller should be ending the game)


def highest_active_other(game, loser_slot):
	forfeited = set(game.get('forfeited_slots', []) or [])
	max_players = game.get('max_players', 2)
	best = None
	best_score = -10 ** 9
	for i in range(max_players):
		if i == loser_slot or i in forfeited:
			continue
		s = game['scores'][i]
		if s > best_score:
			best_score = s
			best = i
	return best


def get_ai_slot(game):
	if not game or not game.get('vs_computer'):
		return None
	slot = game.get('ai_slot')
	if slot is None:
		slot = game.get('max_players', 2) - 1
	return slot


TILE_DISTRIBUTION = {
	'A':9,'B':2,'C':2,'D':4,'E':12,'F':2,'G':3,'H':2,'I':9,'J':1,
	'K':1,'L':4,'M':2,'N':6,'O':8,'P':2,'Q':1,'R':6,'S':4,'T':6,
	'U':4,'V':2,'W':2,'X':1,'Y':2,'Z':1,'_':2,
}
TILE_PALETTE = [
	'#ffe49f',
	'#ffd0d8',
	'#d4f0d0',
	'#cfe4f7',
	'#e6d6f5',
	'#ffd9b8',
	'#c5e8e0',
	'#f5e0a8',
]


def pick_color_set(n):
	n = max(2, min(MAX_PLAYERS_CAP, n))
	return random.sample(TILE_PALETTE, n)


def new_game(mode='classic', speed=False, max_players=2, challenge_mode=False, transparent_mode=False,
			 vs_computer=False, ai_difficulty=50, ai_style='score'):
	max_players = max(MIN_PLAYERS, min(MAX_PLAYERS_CAP, int(max_players or 2)))
	bag = []
	for letter, count in TILE_DISTRIBUTION.items():
		bag.extend([letter] * count)
	random.shuffle(bag)
	racks = [[bag.pop() for _ in range(7)] for _ in range(max_players)]
	return {
		'mode': mode if mode in ('classic', 'random') else 'classic',
		'speed_mode': bool(speed),
		'challenge_mode': bool(challenge_mode),
		'transparent_mode': bool(transparent_mode),
		'pending_views': {},
		'max_players': max_players,
		'turn_deadline': None,
		'missed_turns': [0] * max_players,
		'max_turns': SPEED_MAX_TURNS,
		'bonus_board': make_bonus_board(mode),
		'board': [['' for _ in range(15)] for _ in range(15)],
		'blanks': [[False for _ in range(15)] for _ in range(15)],
		'tile_owners': [[None for _ in range(15)] for _ in range(15)],
		'bag': bag,
		'racks': racks,
		'scores': [0] * max_players,
		'moves': [0] * max_players,
		'bluffs': [0] * max_players,
		'bluff_points': [0] * max_players,
		'current': 0,
		'first_move': True,
		'last_words': [],
		'last_score': 0,
		'last_player': None,
		'last_action': None,
		'last_definitions': [],
		'last_placements': [],
		'last_word_cells': [],
		'last_play_undo': None,
		'consecutive_passes': 0,
		'started_at': None,
		'ended_at': None,
		'game_over': False,
		'game_result': None,
		'final_summary': None,
		'forfeited_slots': [],
		'history': [],
		'vs_computer': bool(vs_computer),
		'ai_difficulty': max(1, min(100, int(ai_difficulty or 50))),
		'ai_style': ai_style if ai_style in ('score', 'stacker') else 'score',
		'ai_play_after': 0,
		'ai_slot': (max_players - 1) if vs_computer else None,
		'no_moves_slot': None,
	}


def unseen_counts(game, my_slot):
	counts = {}
	for letter in game['bag']:
		counts[letter] = counts.get(letter, 0) + 1
	if my_slot == SPECTATOR_SLOT:
		return counts
	max_players = game.get('max_players', 2)
	for i in range(max_players):
		if i == my_slot:
			continue
		for letter in game['racks'][i]:
			counts[letter] = counts.get(letter, 0) + 1
	return counts


def clean_name(raw):
	if not raw:
		return ''
	s = ''.join(ch for ch in str(raw) if ch.isalpha())[:9]
	return s.upper()


def new_room(mode='classic', speed=False, max_players=2, challenge_mode=False, transparent_mode=False,
			 vs_computer=False, ai_difficulty=50, ai_style='score'):
	if vs_computer:
		speed = False
		challenge_mode = False
		transparent_mode = False
	max_players = max(MIN_PLAYERS, min(MAX_PLAYERS_CAP, int(max_players or 2)))
	now = time.time()
	return {
		'game': new_game(mode=mode, speed=speed, max_players=max_players, challenge_mode=challenge_mode, transparent_mode=transparent_mode,
						 vs_computer=vs_computer, ai_difficulty=ai_difficulty, ai_style=ai_style),
		'players': {},
		'names': [''] * max_players,
		'user_ids': [None] * max_players,
		'colors': pick_color_set(max_players),
		'chat': [],
		'chat_seq': 0,
		'mode': mode,
		'speed': speed,
		'challenge_mode': bool(challenge_mode),
		'transparent_mode': bool(transparent_mode),
		'vs_computer': bool(vs_computer),
		'ai_difficulty': max(1, min(100, int(ai_difficulty or 50))),
		'ai_style': ai_style if ai_style in ('score', 'stacker') else 'score',
		'max_players': max_players,
		'spectators': {},
		'rematch': [False] * max_players,
		'created_at': now,
		'last_activity': now,
	}


SPECTATOR_SLOT = -1


def filled_slots(room):
	"""Slots held by a player token or already forfeited."""
	return set(room['players'].values()) | set(room['game'].get('forfeited_slots', []) or [])


def clean_placements(raw):
	"""Return validated placements, or None if any entry is malformed."""
	if not isinstance(raw, list) or len(raw) > 7:
		return None
	cleaned = []
	seen = set()
	for p in raw:
		try:
			r, c = int(p['row']), int(p['col'])
			letter = str(p['letter']).upper()
		except (KeyError, TypeError, ValueError):
			return None
		if not (0 <= r < 15 and 0 <= c < 15) or len(letter) != 1 or letter not in move_gen.ALPHA or (r, c) in seen:
			return None
		seen.add((r, c))
		cleaned.append({'row': r, 'col': c, 'letter': letter, 'blank': bool(p.get('blank', False))})
	return cleaned


def refill_rack(game, player):
	rack = game['racks'][player]
	while len(rack) < 7 and game['bag']:
		rack.append(game['bag'].pop())


def player_has_moves(game, slot):
	if game['game_over']:
		return False
	rack = game['racks'][slot]
	if not rack:
		return False
	return move_gen.has_any_move(
		game['board'], rack, game['bonus_board'], game['blanks'],
		WORD_TRIE, DICTIONARY, first_move=game['first_move'],
	)


def rack_value(rack):
	return sum(move_gen.LETTER_VALUES.get(letter, 0) for letter in rack)


def set_last_action(game, slot, action):
	"""Record a turn that placed no tiles (pass, swap, timeout)."""
	game['last_words'] = []
	game['last_score'] = 0
	game['last_player'] = slot
	game['last_action'] = action
	game['last_definitions'] = []
	game['last_placements'] = []
	game['last_word_cells'] = []
	game['last_play_undo'] = None


def check_placement(game, placements):
	"""Check where cleaned placements land.

	Returns (status, horizontal, board_after). status is None for a straight, gap-free line that
	covers a star or touches an existing tile; otherwise one of occupied, dead_cell, not_aligned,
	gap or disconnected. horizontal and board_after are None for the first three.
	"""
	for p in placements:
		if game['board'][p['row']][p['col']] != '':
			return 'occupied', None, None
		if game['bonus_board'][p['row']][p['col']] == 'XX':
			return 'dead_cell', None, None
	rows = {p['row'] for p in placements}
	cols = {p['col'] for p in placements}
	if len(rows) > 1 and len(cols) > 1:
		return 'not_aligned', None, None
	horizontal = len(rows) == 1
	board_after = [row[:] for row in game['board']]
	for p in placements:
		board_after[p['row']][p['col']] = p['letter']
	if horizontal:
		r = next(iter(rows))
		line = [board_after[r][c] for c in range(min(cols), max(cols) + 1)]
	else:
		c = next(iter(cols))
		line = [board_after[r][c] for r in range(min(rows), max(rows) + 1)]
	if '' in line:
		return 'gap', horizontal, board_after
	if any(game['bonus_board'][p['row']][p['col']] == 'ST' for p in placements):
		return None, horizontal, board_after
	if not game['first_move']:
		for p in placements:
			for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
				nr, nc = p['row'] + dr, p['col'] + dc
				if 0 <= nr < 15 and 0 <= nc < 15 and game['board'][nr][nc] != '':
					return None, horizontal, board_after
	return 'disconnected', horizontal, board_after


def placement_words(game, placements, horizontal, board_after):
	"""Words formed by a placement and the score they earn."""
	words = move_gen.extract_words(board_after, [(p['row'], p['col']) for p in placements], horizontal)
	new_blanks = {(p['row'], p['col']) for p in placements if p.get('blank')}
	score = move_gen.score_words(words, new_blanks, game['blanks'], game['bonus_board'], len(placements)) if words else 0
	return words, new_blanks, score


def apply_play(room, slot, placements):
	"""Put a validated play on the board, score it, refill the rack and end the turn."""
	game = room['game']
	max_players = game.get('max_players', 2)
	challenge_mode = bool(game.get('challenge_mode'))
	_status, horizontal, board_after = check_placement(game, placements)
	words, new_blanks, score = placement_words(game, placements, horizontal, board_after)
	word_list = [w for w, _ in words]
	was_first_move = game['first_move']
	rack = game['racks'][slot]
	for p in placements:
		rack.remove('_' if p.get('blank') else p['letter'])
	game['board'] = board_after
	for p in placements:
		if p.get('blank'):
			game['blanks'][p['row']][p['col']] = True
		game['tile_owners'][p['row']][p['col']] = slot
	game['scores'][slot] += score
	game['first_move'] = False
	bluff_points_added = 0
	if challenge_mode:
		invalid = [cells for w, cells in words if not check_dictionary(w)]
		if invalid:
			game.setdefault('bluffs', [0] * max_players)
			game.setdefault('bluff_points', [0] * max_players)
			bluff_points_added = sum(move_gen.score_word(cells, new_blanks, game['blanks'], game['bonus_board']) for cells in invalid)
			game['bluffs'][slot] += len(invalid)
			game['bluff_points'][slot] += bluff_points_added
	if any(w.upper() == 'PARTY' for w in word_list):
		game['party_played_at'] = int(time.time())
	game['consecutive_passes'] = 0
	game['last_words'] = word_list
	game['last_score'] = score
	game['last_player'] = slot
	game['last_action'] = 'play'
	game['last_definitions'] = [] if challenge_mode else [{'word': w, 'entry': fetch_definition(w)} for w in word_list]
	game['last_placements'] = [{'row': p['row'], 'col': p['col']} for p in placements]
	game['last_word_cells'] = [{'row': r, 'col': c} for r, c in {(r, c) for _w, cells in words for r, c, _l, _new in cells}]
	game['moves'][slot] += 1
	bag_before_refill = list(game['bag'])
	refill_rack(game, slot)
	game['last_play_undo'] = {
		'player': slot,
		'placed': [{'row': p['row'], 'col': p['col'], 'letter': p['letter'], 'blank': bool(p.get('blank')), 'rack_letter': '_' if p.get('blank') else p['letter']} for p in placements],
		'drawn': bag_before_refill[len(game['bag']):],
		'score_delta': score,
		'bluff_points_delta': bluff_points_added,
		'was_first_move': was_first_move,
	} if challenge_mode else None
	record_history(room, slot, 'play', words=word_list, score=score)
	user_ids = room.get('user_ids') or []
	try:
		record_play_metrics([w for w in word_list if check_dictionary(w)], room['names'][slot] or f'P{slot + 1}', score, room.get('_rid'), game.get('mode', 'classic'), user_id=user_ids[slot] if slot < len(user_ids) else None)
	except Exception:
		pass
	end_turn(room, slot)
	return score, word_list


def end_turn(room, slot):
	"""Finish the game if it's over, otherwise pass the turn on."""
	game = room['game']
	if not game['racks'][slot] and not game['bag']:
		for i in range(game.get('max_players', 2)):
			if i != slot:
				penalty = rack_value(game['racks'][i])
				game['scores'][i] -= penalty
				game['scores'][slot] += penalty
		finalize_game(room, 'tiles_out')
	elif game['consecutive_passes'] >= 3:
		finalize_game(room, 'three_passes')
	else:
		advance_turn(game)
		if game.get('vs_computer') and game['current'] == get_ai_slot(game):
			game['ai_play_after'] = time.time() + AI_THINK_SECONDS


def _ai_swap_or_pass(room):
	game = room['game']
	ai_slot = get_ai_slot(game)
	bag = game['bag']
	rack = game['racks'][ai_slot]
	if len(bag) >= 7 and rack:
		game['racks'][ai_slot] = [bag.pop() for _ in rack]
		bag.extend(rack)
		random.shuffle(bag)
		game['consecutive_passes'] = 0
		set_last_action(game, ai_slot, 'swap')
	else:
		game['consecutive_passes'] += 1
		set_last_action(game, ai_slot, 'pass')
	game['moves'][ai_slot] += 1
	record_history(room, ai_slot, game['last_action'])


def ai_take_turn(room):
	"""Run the AI's turn. Mutates room state, advances current player."""
	game = room['game']
	ai_slot = get_ai_slot(game)
	if ai_slot is None:
		return
	if game['game_over']:
		return
	if game['current'] != ai_slot:
		return
	rack = game['racks'][ai_slot]
	moves = move_gen.generate_moves(
		game['board'], rack, game['bonus_board'], game['blanks'],
		WORD_TRIE, DICTIONARY, first_move=game['first_move'],
	)
	if game.get('ai_style') == 'stacker':
		chosen = move_gen.pick_stacker_move(moves)
	else:
		chosen = move_gen.pick_move_by_difficulty(moves, game.get('ai_difficulty', 50))
	if chosen is not None:
		apply_play(room, ai_slot, chosen['placements'])
	else:
		_ai_swap_or_pass(room)
		end_turn(room, ai_slot)
