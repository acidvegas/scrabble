# scrabble - Developed by acidvegas in Python (https://github.com/acidvegas)
# boards.py

import math
import random


BONUS_BOARD = [
	['TW','','','DL','','','','TW','','','','DL','','','TW'],
	['','DW','','','','TL','','','','TL','','','','DW',''],
	['','','DW','','','','DL','','DL','','','','DW','',''],
	['DL','','','DW','','','','DL','','','','DW','','','DL'],
	['','','','','DW','','','','','','DW','','','',''],
	['','TL','','','','TL','','','','TL','','','','TL',''],
	['','','DL','','','','DL','','DL','','','','DL','',''],
	['TW','','','DL','','','','ST','','','','DL','','','TW'],
	['','','DL','','','','DL','','DL','','','','DL','',''],
	['','TL','','','','TL','','','','TL','','','','TL',''],
	['','','','','DW','','','','','','DW','','','',''],
	['DL','','','DW','','','','DL','','','','DW','','','DL'],
	['','','DW','','','','DL','','DL','','','','DW','',''],
	['','DW','','','','TL','','','','TL','','','','DW',''],
	['TW','','','DL','','','','TW','','','','DL','','','TW'],
]
SAFE_ZONE = {(r, c) for r in range(6, 9) for c in range(6, 9)}


def fourfold(r, c):
	dr, dc = r - 7, c - 7
	return {(7 + dr, 7 + dc), (7 + dr, 7 - dc), (7 - dr, 7 + dc), (7 - dr, 7 - dc)}


def eightfold(r, c):
	dr, dc = r - 7, c - 7
	return fourfold(r, c) | fourfold(7 + dc, 7 + dr)


def in_bounds(rc):
	r, c = rc
	return 0 <= r < 15 and 0 <= c < 15


def dead_diamond():
	k = random.randint(3, 6)
	return {(r, c) for r in range(15) for c in range(15) if abs(r - 7) + abs(c - 7) == k} - SAFE_ZONE


def dead_x_cross():
	w = random.randint(0, 1)
	return {(r, c) for r in range(15) for c in range(15)
			if abs(r - c) <= w or abs(r + c - 14) <= w} - SAFE_ZONE


def dead_plus_cross():
	w = random.randint(0, 1)
	return {(r, c) for r in range(15) for c in range(15)
			if abs(r - 7) <= w or abs(c - 7) <= w} - SAFE_ZONE


def dead_box_frame():
	k = random.randint(3, 6)
	return {(r, c) for r in range(15) for c in range(15)
			if max(abs(r - 7), abs(c - 7)) == k} - SAFE_ZONE


def dead_corner_cuts():
	size = random.randint(2, 4)
	cells = set()
	for r in range(size):
		for c in range(size):
			if r + c < size:
				for tr, tc in [(r, c), (r, 14 - c), (14 - r, c), (14 - r, 14 - c)]:
					cells.add((tr, tc))
	return cells - SAFE_ZONE


def dead_random_clusters():
	cells = set()
	for _ in range(random.randint(2, 5)):
		start = None
		for _ in range(30):
			cand = (random.randint(0, 14), random.randint(0, 14))
			if cand not in SAFE_ZONE and cand not in cells:
				start = cand
				break
		if not start:
			continue
		cluster = [start]
		cells.add(start)
		target = random.randint(2, 7)
		while len(cluster) < target:
			nr, nc = cluster[-1]
			stepped = False
			for _ in range(8):
				dr, dc = random.choice([(-1, 0), (1, 0), (0, -1), (0, 1)])
				tr, tc = nr + dr, nc + dc
				if 0 <= tr < 15 and 0 <= tc < 15 and (tr, tc) not in cells and (tr, tc) not in SAFE_ZONE:
					cluster.append((tr, tc))
					cells.add((tr, tc))
					stepped = True
					break
			if not stepped:
				break
	return cells


def dead_symmetric_random():
	cells = set()
	for _ in range(random.randint(3, 8)):
		r, c = random.randint(0, 7), random.randint(0, 7)
		for sym in eightfold(r, c):
			if in_bounds(sym):
				cells.add(sym)
	return cells - SAFE_ZONE


def dead_sparse_dots():
	cells = set()
	for _ in range(random.randint(4, 9)):
		r, c = random.randint(0, 6), random.randint(0, 6)
		for sym in fourfold(r, c):
			if in_bounds(sym):
				cells.add(sym)
	return cells - SAFE_ZONE


def dead_rings():
	cells = set()
	for _ in range(random.randint(1, 3)):
		k = random.randint(2, 6)
		for r in range(15):
			for c in range(15):
				if abs(r - 7) + abs(c - 7) == k:
					cells.add((r, c))
	return cells - SAFE_ZONE


def dead_wave():
	freq = 0.4 + random.random() * 0.6
	phase = random.random() * math.tau
	threshold = 0.7 + random.random() * 0.2
	cells = set()
	for r in range(15):
		for c in range(15):
			if math.sin((r + c) * freq + phase) > threshold:
				cells.add((r, c))
	return cells - SAFE_ZONE


def dead_checker_region():
	cx, cy = random.randint(2, 12), random.randint(2, 12)
	radius = random.randint(2, 4)
	cells = set()
	for r in range(max(0, cy - radius), min(15, cy + radius + 1)):
		for c in range(max(0, cx - radius), min(15, cx + radius + 1)):
			if (r + c) % 2 == 0:
				cells.add((r, c))
	return cells - SAFE_ZONE


def dead_spiral():
	cells = set()
	steps = random.randint(20, 60)
	r, c = random.choice([(0, 0), (0, 14), (14, 0), (14, 14)])
	direction = 0
	dirs = [(0, 1), (1, 0), (0, -1), (-1, 0)]
	step_size = 1
	side = 0
	for _ in range(steps):
		if in_bounds((r, c)) and (r, c) not in SAFE_ZONE:
			cells.add((r, c))
		dr, dc = dirs[direction]
		r += dr
		c += dc
		side += 1
		if side >= step_size:
			direction = (direction + 1) % 4
			side = 0
			if direction % 2 == 0:
				step_size += 1
	return cells


DEAD_GENERATORS = [
	dead_diamond, dead_x_cross, dead_plus_cross, dead_box_frame, dead_corner_cuts,
	dead_random_clusters, dead_symmetric_random, dead_sparse_dots, dead_rings,
	dead_wave, dead_checker_region, dead_spiral, lambda: set(),
]


def find_components(playable):
	components = []
	visited = set()
	for cell in playable:
		if cell in visited:
			continue
		comp = set()
		stack = [cell]
		while stack:
			cur = stack.pop()
			if cur in comp:
				continue
			comp.add(cur)
			r, c = cur
			for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
				nb = (r + dr, c + dc)
				if nb in playable and nb not in comp:
					stack.append(nb)
		visited |= comp
		components.append(comp)
	return components


def ensure_connectivity(dead_cells):
	dead_cells = set(dead_cells)
	all_cells = {(r, c) for r in range(15) for c in range(15)}
	for _ in range(25):
		playable = all_cells - dead_cells
		components = find_components(playable)
		if len(components) <= 1:
			return dead_cells
		components.sort(key=len, reverse=True)
		main, other = components[0], components[1]
		starts = set()
		for r, c in main:
			for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
				nb = (r + dr, c + dc)
				if nb in dead_cells:
					starts.add(nb)
		if not starts:
			for r, c in other:
				for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
					nb = (r + dr, c + dc)
					if nb in dead_cells:
						dead_cells.discard(nb)
						break
				else:
					continue
				break
			continue
		queue = [(s, [s]) for s in starts]
		visited = set(starts)
		found_path = None
		while queue and found_path is None:
			cur, path = queue.pop(0)
			r, c = cur
			for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
				nb = (r + dr, c + dc)
				if nb in other:
					found_path = path
					break
				if nb in dead_cells and nb not in visited:
					visited.add(nb)
					queue.append((nb, path + [nb]))
		if found_path:
			dead_cells -= set(found_path)
		else:
			break
	return dead_cells


def add_random_stars(board, dead_cells):
	if random.random() > 1.0 / 3.0:
		return
	extra = random.randint(1, 2)
	candidates = [(r, c) for r in range(15) for c in range(15)
				  if (r, c) not in dead_cells and board[r][c] in ('', 'DL', 'DW', 'TL', 'TW')]
	random.shuffle(candidates)
	placed = []
	for r, c in candidates:
		if len(placed) >= extra:
			break
		if (r, c) == (7, 7):
			continue
		too_close = False
		for tr, tc in placed + [(7, 7)]:
			if abs(tr - r) + abs(tc - c) < 4:
				too_close = True
				break
		if too_close:
			continue
		board[r][c] = 'ST'
		placed.append((r, c))


def place_bonuses(dead_cells, pattern):
	board = [['' for _ in range(15)] for _ in range(15)]
	board[7][7] = 'ST'
	placed = {(7, 7)} | set(dead_cells)
	specs = [
		('TW', random.randint(5, 10)),
		('DW', random.randint(12, 18)),
		('TL', random.randint(8, 14)),
		('DL', random.randint(18, 26)),
	]

	def try_random(kind, count, attempts_per=30):
		for _ in range(count):
			for _ in range(attempts_per):
				r, c = random.randint(0, 14), random.randint(0, 14)
				if (r, c) not in placed:
					board[r][c] = kind
					placed.add((r, c))
					break

	def try_symmetric(kind, target_groups, fold_fn):
		groups_placed = 0
		for _ in range(target_groups * 4):
			if groups_placed >= target_groups:
				break
			r, c = random.randint(0, 7), random.randint(0, 7)
			syms = [s for s in fold_fn(r, c) if in_bounds(s)]
			if all(s not in placed for s in syms):
				for s in syms:
					board[s[0]][s[1]] = kind
					placed.add(s)
				groups_placed += 1

	if pattern == 'mirror_4':
		for kind, count in specs:
			try_symmetric(kind, max(1, count // 4), fourfold)
			try_random(kind, max(0, count - sum(row.count(kind) for row in board)))
	elif pattern == 'mirror_8':
		for kind, count in specs:
			try_symmetric(kind, max(1, count // 8), eightfold)
			try_random(kind, max(0, count - sum(row.count(kind) for row in board)))
	elif pattern == 'rings':
		ring_map = {}
		for kind in ['TW', 'DW', 'TL', 'DL']:
			ring_map[random.choice([2, 3, 4, 5, 6, 7])] = kind
		for r in range(15):
			for c in range(15):
				d = abs(r - 7) + abs(c - 7)
				if d in ring_map and (r, c) not in placed and random.random() < 0.55:
					board[r][c] = ring_map[d]
					placed.add((r, c))
		for kind, count in specs:
			existing = sum(row.count(kind) for row in board)
			try_random(kind, max(0, count - existing))
	elif pattern == 'clusters':
		hot_zones = [(random.randint(2, 12), random.randint(2, 12)) for _ in range(random.randint(2, 4))]
		for kind, count in specs:
			for _ in range(count):
				for _ in range(30):
					if hot_zones and random.random() < 0.5:
						hz = random.choice(hot_zones)
						r = max(0, min(14, hz[0] + random.randint(-2, 2)))
						c = max(0, min(14, hz[1] + random.randint(-2, 2)))
					else:
						r, c = random.randint(0, 14), random.randint(0, 14)
					if (r, c) not in placed:
						board[r][c] = kind
						placed.add((r, c))
						break
	else:
		for kind, count in specs:
			try_random(kind, count, attempts_per=50)
	return board


def generate_random_bonus_board():
	dead = random.choice(DEAD_GENERATORS)()
	dead = {rc for rc in dead if in_bounds(rc) and rc not in SAFE_ZONE}
	if len(dead) > 45:
		dead = set(random.sample(list(dead), 45))
	dead = ensure_connectivity(dead)
	pattern = random.choice(['mirror_4', 'mirror_8', 'rings', 'clusters', 'random'])
	board = place_bonuses(dead, pattern)
	for r, c in dead:
		board[r][c] = 'XX'
	add_random_stars(board, dead)
	if random.random() < 0.1:
		add_zero_letter_tiles(board, dead)
	return board


def add_zero_letter_tiles(board, dead_cells):
	candidates = [(r, c) for r in range(15) for c in range(15)
				  if (r, c) not in dead_cells
				  and (r, c) not in SAFE_ZONE
				  and board[r][c] not in ('ST', 'XX', 'ZL')]
	random.shuffle(candidates)
	n = random.randint(1, 3)
	for r, c in candidates[:n]:
		board[r][c] = 'ZL'


def make_bonus_board(mode):
	if mode == 'random':
		return generate_random_bonus_board()
	return [row[:] for row in BONUS_BOARD]
