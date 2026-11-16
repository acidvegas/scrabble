# scrabble - Developed by acidvegas in Python (https://github.com/acidvegas)
# move_gen.py

"""Scrabble move generator (trie + anchor search)."""

import time
from typing import Iterable

LETTER_VALUES = {
	'A':1,'B':3,'C':3,'D':2,'E':1,'F':4,'G':2,'H':4,'I':1,'J':8,
	'K':5,'L':1,'M':3,'N':1,'O':1,'P':3,'Q':10,'R':1,'S':1,'T':1,
	'U':1,'V':4,'W':4,'X':8,'Y':4,'Z':10,'_':0,
}

ALPHA = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
SEARCH_SECONDS = 1.0 # longest a full move search runs (horizontal gets the first half); it keeps the moves found so far


class _OutOfTime(Exception):
	"""Raised inside the search once its time is up."""


class _Results(list):
	"""Moves found so far, and when the search has to stop."""
	deadline = float('inf')


class TrieNode:
	__slots__ = ('children', 'is_word')

	def __init__(self):
		self.children = {}
		self.is_word = False


def build_trie(words: Iterable[str]) -> TrieNode:
	root = TrieNode()
	for w in words:
		node = root
		for ch in w.upper():
			if ch not in ALPHA:
				node = None
				break
			child = node.children.get(ch)
			if child is None:
				child = TrieNode()
				node.children[ch] = child
			node = child
		if node is not None:
			node.is_word = True
	return root


def _read_existing_word(board, r, c, dr, dc):
	"""Read a contiguous letter sequence from (r,c) extending in (dr,dc)."""
	letters = []
	rr, cc = r, c
	while 0 <= rr < 15 and 0 <= cc < 15 and board[rr][cc]:
		letters.append(board[rr][cc])
		rr += dr
		cc += dc
	return letters


def _compute_cross_checks(board, dictionary_set, horizontal):
	"""For each empty cell, compute set of letters that form a valid cross-word.

	For horizontal play, cross-words are vertical (and vice versa).
	Returns dict[(r,c)] -> (allowed_set, cross_score) where cross_score is the
	sum of letter values of existing tiles in the cross-word at that cell
	(used for scoring). Cells with no existing tile neighbors in the cross
	direction map to (full_alphabet, 0).
	"""
	cross = {}
	if horizontal:
		cross_dr, cross_dc = -1, 0
		other_dr, other_dc = 1, 0
	else:
		cross_dr, cross_dc = 0, -1
		other_dr, other_dc = 0, 1
	for r in range(15):
		for c in range(15):
			if board[r][c]:
				continue
			top = _read_existing_word(board, r + cross_dr, c + cross_dc, cross_dr, cross_dc)
			top.reverse()
			bottom = _read_existing_word(board, r + other_dr, c + other_dc, other_dr, other_dc)
			if not top and not bottom:
				cross[(r, c)] = (set(ALPHA), 0)
				continue
			existing_score = sum(LETTER_VALUES.get(ch, 0) for ch in top) + sum(LETTER_VALUES.get(ch, 0) for ch in bottom)
			allowed = set()
			top_s = ''.join(top)
			bottom_s = ''.join(bottom)
			for ch in ALPHA:
				if (top_s + ch + bottom_s) in dictionary_set:
					allowed.add(ch)
			cross[(r, c)] = (allowed, existing_score)
	return cross


def _find_anchors(board, bonus_board, first_move):
	"""Anchors are empty cells from which the move-search may begin.

	A play is legal if it either (a) connects to an existing tile, or (b) covers
	an unused star square. So anchors include every empty cell that is adjacent
	to an existing tile PLUS every empty star ('ST') cell. On the first move,
	only the star squares qualify (there are no existing tiles yet).
	"""
	star_cells = []
	if bonus_board:
		for r in range(15):
			row = bonus_board[r]
			for c in range(15):
				if row[c] == 'ST' and not board[r][c]:
					star_cells.append((r, c))
	if first_move:
		return star_cells if star_cells else [(7, 7)]
	seen = set()
	anchors = []
	for r in range(15):
		for c in range(15):
			if board[r][c]:
				continue
			for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
				nr, nc = r + dr, c + dc
				if 0 <= nr < 15 and 0 <= nc < 15 and board[nr][nc]:
					anchors.append((r, c))
					seen.add((r, c))
					break
	for sc in star_cells:
		if sc not in seen:
			anchors.append(sc)
	return anchors


def _max_left_empty(board, anchor_r, anchor_c, horizontal, anchors_set):
	"""How far left/up we can extend from the anchor without hitting another anchor or tile."""
	dr, dc = (0, -1) if horizontal else (-1, 0)
	count = 0
	r, c = anchor_r + dr, anchor_c + dc
	while 0 <= r < 15 and 0 <= c < 15 and not board[r][c] and (r, c) not in anchors_set:
		count += 1
		r += dr
		c += dc
	return count


def score_word(cells, new_blanks, blanks_board, bonus_board):
	"""Score one word. cells is a list of (r, c, letter, is_new); new_blanks holds the (r, c) of newly placed blanks."""
	word_mult = 1
	word_score = 0
	for r, c, letter, is_new in cells:
		is_blank = (r, c) in new_blanks if is_new else blanks_board[r][c]
		letter_val = 0 if is_blank else LETTER_VALUES.get(letter, 0)
		mult = 1
		if is_new:
			b = bonus_board[r][c]
			if b == 'ZL':
				letter_val = 0
			elif b == 'DL':
				mult = 2
			elif b == 'TL':
				mult = 3
			elif b in ('DW', 'ST'):
				word_mult *= 2
			elif b == 'TW':
				word_mult *= 3
		word_score += letter_val * mult
	return word_score * word_mult


def score_words(words, new_blanks, blanks_board, bonus_board, tiles_placed):
	"""Score every word from one play, plus the 50 point bonus for using all 7 tiles."""
	total = sum(score_word(cells, new_blanks, blanks_board, bonus_board) for _word, cells in words)
	if tiles_placed == 7:
		total += 50
	return total


def extract_words(board_after, placements, horizontal):
	"""After applying placements to a board, extract the main word + cross-words.
	placements only needs (row, col) at the front of each entry.
	Returns list of (word_string, [(r,c,letter,is_new), ...])."""
	placement_set = {(p[0], p[1]) for p in placements}
	out = []

	if horizontal:
		r = placements[0][0]
		c_min = min(p[1] for p in placements)
		c_max = max(p[1] for p in placements)
		cc = c_min
		while cc > 0 and board_after[r][cc - 1]:
			cc -= 1
		end = c_max
		while end < 14 and board_after[r][end + 1]:
			end += 1
		cells = []
		word = []
		for c in range(cc, end + 1):
			letter = board_after[r][c]
			cells.append((r, c, letter, (r, c) in placement_set))
			word.append(letter)
		if len(word) >= 2:
			out.append((''.join(word), cells))
		for p in placements:
			r2, c2 = p[0], p[1]
			rr = r2
			while rr > 0 and board_after[rr - 1][c2]:
				rr -= 1
			end_r = r2
			while end_r < 14 and board_after[end_r + 1][c2]:
				end_r += 1
			if end_r > rr:
				cells2 = []
				word2 = []
				for ri in range(rr, end_r + 1):
					letter = board_after[ri][c2]
					cells2.append((ri, c2, letter, (ri, c2) in placement_set))
					word2.append(letter)
				out.append((''.join(word2), cells2))
	else:
		c = placements[0][1]
		r_min = min(p[0] for p in placements)
		r_max = max(p[0] for p in placements)
		rr = r_min
		while rr > 0 and board_after[rr - 1][c]:
			rr -= 1
		end = r_max
		while end < 14 and board_after[end + 1][c]:
			end += 1
		cells = []
		word = []
		for r in range(rr, end + 1):
			letter = board_after[r][c]
			cells.append((r, c, letter, (r, c) in placement_set))
			word.append(letter)
		if len(word) >= 2:
			out.append((''.join(word), cells))
		for p in placements:
			r2, c2 = p[0], p[1]
			cc = c2
			while cc > 0 and board_after[r2][cc - 1]:
				cc -= 1
			end_c = c2
			while end_c < 14 and board_after[r2][end_c + 1]:
				end_c += 1
			if end_c > cc:
				cells2 = []
				word2 = []
				for ci in range(cc, end_c + 1):
					letter = board_after[r2][ci]
					cells2.append((r2, ci, letter, (r2, ci) in placement_set))
					word2.append(letter)
				out.append((''.join(word2), cells2))
	return out


def _gen_for_direction(board, rack, bonus_board, blanks_board, trie, dictionary_set,
					   first_move, horizontal, results, only_check_exists):
	"""Generate moves in one direction (horizontal=True or vertical=False)."""
	anchors = _find_anchors(board, bonus_board, first_move)
	anchors_set = set(anchors)
	cross = _compute_cross_checks(board, dictionary_set, horizontal)
	if horizontal:
		step_dr, step_dc = 0, 1
		back_dr, back_dc = 0, -1
	else:
		step_dr, step_dc = 1, 0
		back_dr, back_dc = -1, 0

	for anchor_r, anchor_c in anchors:
		if only_check_exists and results:
			return
		# If there's an existing tile immediately before the anchor, the prefix is fixed.
		pr, pc = anchor_r + back_dr, anchor_c + back_dc
		if 0 <= pr < 15 and 0 <= pc < 15 and board[pr][pc]:
			# Walk back to find start of existing prefix
			sr, sc = pr, pc
			while 0 <= sr + back_dr < 15 and 0 <= sc + back_dc < 15 and board[sr + back_dr][sc + back_dc]:
				sr += back_dr
				sc += back_dc
			# Walk the trie through the existing prefix
			node = trie
			ok = True
			rr, cc = sr, sc
			while (rr, cc) != (anchor_r, anchor_c):
				letter = board[rr][cc]
				if letter not in node.children:
					ok = False
					break
				node = node.children[letter]
				rr += step_dr
				cc += step_dc
			if not ok:
				continue
			_extend_right(anchor_r, anchor_c, anchor_r, anchor_c, node, rack, [],
						  board, bonus_board, blanks_board, trie, dictionary_set,
						  horizontal, cross, results, only_check_exists, step_dr, step_dc)
		else:
			max_left = _max_left_empty(board, anchor_r, anchor_c, horizontal, anchors_set)
			_left_part(anchor_r, anchor_c, anchor_r, anchor_c, trie, rack, [],
					   max_left, board, bonus_board, blanks_board, trie, dictionary_set,
					   horizontal, cross, results, only_check_exists,
					   step_dr, step_dc, back_dr, back_dc)


def _left_part(start_r, start_c, anchor_r, anchor_c, node, rack, placements, limit,
			   board, bonus_board, blanks_board, trie, dictionary_set,
			   horizontal, cross, results, only_check_exists,
			   step_dr, step_dc, back_dr, back_dc):
	if only_check_exists and results:
		return
	if time.monotonic() > results.deadline:
		raise _OutOfTime
	_extend_right(anchor_r, anchor_c, anchor_r, anchor_c, node, rack, placements,
				  board, bonus_board, blanks_board, trie, dictionary_set,
				  horizontal, cross, results, only_check_exists, step_dr, step_dc)
	if limit == 0:
		return
	if only_check_exists and results:
		return
	# Place a letter from the rack to the left of start
	new_r, new_c = start_r + back_dr, start_c + back_dc
	if not (0 <= new_r < 15 and 0 <= new_c < 15):
		return
	if board[new_r][new_c]:
		return
	if bonus_board[new_r][new_c] == 'XX':
		return
	cross_allowed, _cs = cross.get((new_r, new_c), (set(ALPHA), 0))
	seen = set()
	for idx, tile in enumerate(rack):
		if tile in seen:
			continue
		seen.add(tile)
		if tile == '_':
			for letter in node.children:
				if letter not in cross_allowed:
					continue
				new_rack = rack[:idx] + rack[idx + 1:]
				new_placements = [(new_r, new_c, letter, True)] + placements
				_left_part(new_r, new_c, anchor_r, anchor_c, node.children[letter],
						   new_rack, new_placements, limit - 1,
						   board, bonus_board, blanks_board, trie, dictionary_set,
						   horizontal, cross, results, only_check_exists,
						   step_dr, step_dc, back_dr, back_dc)
		else:
			if tile not in node.children:
				continue
			if tile not in cross_allowed:
				continue
			new_rack = rack[:idx] + rack[idx + 1:]
			new_placements = [(new_r, new_c, tile, False)] + placements
			_left_part(new_r, new_c, anchor_r, anchor_c, node.children[tile],
					   new_rack, new_placements, limit - 1,
					   board, bonus_board, blanks_board, trie, dictionary_set,
					   horizontal, cross, results, only_check_exists,
					   step_dr, step_dc, back_dr, back_dc)


def _extend_right(anchor_r, anchor_c, cur_r, cur_c, node, rack, placements,
				  board, bonus_board, blanks_board, trie, dictionary_set,
				  horizontal, cross, results, only_check_exists, step_dr, step_dc):
	if only_check_exists and results:
		return
	if time.monotonic() > results.deadline:
		raise _OutOfTime
	on_board = 0 <= cur_r < 15 and 0 <= cur_c < 15
	if not on_board or not board[cur_r][cur_c]:
		# Record if word ends here
		if node.is_word and placements:
			# Must have crossed the anchor
			crossed = any(_is_at_or_past_anchor(p[0], p[1], anchor_r, anchor_c, step_dr, step_dc) for p in placements)
			if crossed:
				_record_move(placements, board, blanks_board, bonus_board, horizontal,
							 dictionary_set, results)
				if only_check_exists and results:
					return
		if not on_board:
			return
		if bonus_board[cur_r][cur_c] == 'XX':
			return
		cross_allowed, _cs = cross.get((cur_r, cur_c), (set(ALPHA), 0))
		seen = set()
		for idx, tile in enumerate(rack):
			if tile in seen:
				continue
			seen.add(tile)
			if tile == '_':
				for letter in node.children:
					if letter not in cross_allowed:
						continue
					new_rack = rack[:idx] + rack[idx + 1:]
					new_placements = placements + [(cur_r, cur_c, letter, True)]
					_extend_right(anchor_r, anchor_c, cur_r + step_dr, cur_c + step_dc,
								  node.children[letter], new_rack, new_placements,
								  board, bonus_board, blanks_board, trie, dictionary_set,
								  horizontal, cross, results, only_check_exists,
								  step_dr, step_dc)
			else:
				if tile not in node.children:
					continue
				if tile not in cross_allowed:
					continue
				new_rack = rack[:idx] + rack[idx + 1:]
				new_placements = placements + [(cur_r, cur_c, tile, False)]
				_extend_right(anchor_r, anchor_c, cur_r + step_dr, cur_c + step_dc,
							  node.children[tile], new_rack, new_placements,
							  board, bonus_board, blanks_board, trie, dictionary_set,
							  horizontal, cross, results, only_check_exists,
							  step_dr, step_dc)
	else:
		letter = board[cur_r][cur_c]
		child = node.children.get(letter)
		if child is None:
			return
		_extend_right(anchor_r, anchor_c, cur_r + step_dr, cur_c + step_dc,
					  child, rack, placements,
					  board, bonus_board, blanks_board, trie, dictionary_set,
					  horizontal, cross, results, only_check_exists, step_dr, step_dc)


def _is_at_or_past_anchor(r, c, anchor_r, anchor_c, step_dr, step_dc):
	if step_dc == 1:
		return c >= anchor_c
	return r >= anchor_r


def _record_move(placements, board, blanks_board, bonus_board, horizontal,
				 dictionary_set, results):
	board_after = [row[:] for row in board]
	for r, c, letter, _is_blank in placements:
		board_after[r][c] = letter
	words = extract_words(board_after, placements, horizontal)
	if not words:
		return
	for w, _ in words:
		if w not in dictionary_set:
			return
	new_blanks = {(p[0], p[1]) for p in placements if p[3]}
	score = score_words(words, new_blanks, blanks_board, bonus_board, len(placements))
	results.append({
		'placements': [{'row': p[0], 'col': p[1], 'letter': p[2], 'blank': p[3]} for p in placements],
		'score': score,
		'words': [w for w, _ in words],
		'horizontal': horizontal,
	})


def generate_moves(board, rack, bonus_board, blanks_board, trie, dictionary_set,
				   first_move=False, only_check_exists=False):
	"""Generate every legal move for the given rack on the given board.

	Returns a list of dicts with keys: placements, score, words, horizontal.
	If only_check_exists=True, returns as soon as one move is found.
	A full search stops after SEARCH_SECONDS (racks with two blanks can take several seconds) and returns what it found;
	only_check_exists has no time limit, since it ends at the first move and a wrong "no moves" could end the game.
	"""
	results = _Results()
	start = time.monotonic()
	# Horizontal
	if not only_check_exists:
		results.deadline = start + SEARCH_SECONDS / 2
	try:
		_gen_for_direction(board, list(rack), bonus_board, blanks_board, trie, dictionary_set,
						   first_move, True, results, only_check_exists)
	except _OutOfTime:
		pass
	if only_check_exists and results:
		return list(results)
	# Vertical (same anchors logic but with stepping down)
	if not only_check_exists:
		results.deadline = start + SEARCH_SECONDS
	try:
		_gen_for_direction(board, list(rack), bonus_board, blanks_board, trie, dictionary_set,
						   first_move, False, results, only_check_exists)
	except _OutOfTime:
		pass
	return list(results)


def has_any_move(board, rack, bonus_board, blanks_board, trie, dictionary_set, first_move=False):
	return len(generate_moves(board, rack, bonus_board, blanks_board, trie, dictionary_set,
							  first_move=first_move, only_check_exists=True)) > 0


def pick_stacker_move(moves):
	"""Choose the move that forms the most words in one play; tiebreak on score.

	This makes the AI prefer plays that create many simultaneous cross-words
	over plays that just score high, congesting the board with connections.
	"""
	if not moves:
		return None
	return max(moves, key=lambda m: (len(m.get('words', [])), m.get('score', 0)))


def pick_move_by_difficulty(moves, difficulty, rng=None):
	"""Pick a move from a list according to difficulty 1-100.
	100 = always best, 1 = near-worst. Returns None if no moves."""
	import random as _r
	if not moves:
		return None
	rng = rng or _r
	moves_sorted = sorted(moves, key=lambda m: -m['score'])
	n = len(moves_sorted)
	if difficulty >= 100:
		return moves_sorted[0]
	if n == 1:
		return moves_sorted[0]
	# Map difficulty 1..99 to a target rank in [0..n-1]; higher difficulty -> lower rank
	# Use a soft window around the target rank for some variation
	frac = (100 - difficulty) / 99.0  # 0..1
	target = int(round(frac * (n - 1)))
	window = max(1, n // 20)
	low = max(0, target - window)
	high = min(n - 1, target + window)
	return moves_sorted[rng.randint(low, high)]
