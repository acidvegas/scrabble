# scrabble - Developed by acidvegas in Python (https://github.com/acidvegas)
# progress.py

# Player levels and unlockable tile styles. Only finished 2-seat games between a signed in player and the computer count.

import json
import time

from storage import DB_LOCK, db_connect


FEAT_MIN_DIFF   = 30   # play feats and their lifetime counters only count against difficulty 30+
STACKER_DIFF    = 75   # Stacker counts as this difficulty for difficulty conditions
WINDOW_GAMES    = 50   # Wordsmith/Forge: average over this many straight games
FINISHED        = ('tiles_out', 'three_passes', 'no_moves', 'turn_limit')
LOSS_REASONS    = ('forfeit', 'inactive')
DEFAULT_STYLE   = 'classic'

FAMILIES = ['Basics', 'Woods', 'Stone', 'Metals', 'Gems', 'Neon', 'Retro', 'Ocean', 'Nature', 'Sky', 'Space', 'Fire & Ice', 'Paper', 'Candy', 'Fabric', 'Tech', 'Art', 'Mono', 'Luxe', 'Legends']


def level_for(xp: int) -> int:
	'''Level reached with this much XP. Level N needs 100 * N^1.6 total XP.'''

	level = 1
	while xp >= xp_for(level + 1):
		level += 1
	return level


def xp_for(level: int) -> int:
	'''Total XP needed to reach a level.'''

	return 0 if level <= 1 else int(100 * level ** 1.6)


def game_xp(g: dict) -> int:
	'''XP earned by one finished game.'''

	mult = 1.5 if g['stacker'] else 0.1 + 1.9 * (g['raw_diff'] - 1) / 99
	xp = (10 + max(0, g['score']) / 10) * mult * (2 if g['won'] else 1)
	xp += 25 * g['bingos'] + 10 * g['plays50'] + (5 if g['speed'] else 0) + (5 if g['random'] else 0)
	return int(round(xp))


# Each style: (id, name, family, tier, unlock text, condition(c, g), progress(c) -> (current, target) or None)
# c holds the player's lifetime counters after this game (plus 'level' and 'count'), g this game's summary.
def _count(key, n):
	return (lambda c, g: c.get(key, 0) >= n), (lambda c: (c.get(key, 0), n))

def _win(d=0, **flags):
	def cond(c, g):
		return g['won'] and g['diff'] >= d and all(g[k] for k in flags)
	return cond, None

def _feat(fn):
	return (lambda c, g: g['diff'] >= FEAT_MIN_DIFF and fn(g)), None

def _s(sid, name, fam, tier, text, rule):
	cond, prog = rule
	return (sid, name, fam, tier, text, cond, prog)


STYLES = [
	_s('classic',             'Classic',             0,  'common',    'Free',                                                    ((lambda c, g: True), None)),
	_s('mint',                'Mint',                0,  'common',    'Finish 1 AI game',                                        _count('games', 1)),
	_s('pine',                'Pine',                1,  'common',    'Win 10 AI games (diff 20+)',                              _count('wins20', 10)),
	_s('oak',                 'Oak',                 1,  'uncommon',  'Play 100 AI games',                                       _count('games', 100)),
	_s('walnut',              'Walnut',              1,  'rare',      'Win 100 AI games (diff 40+)',                             _count('wins40', 100)),
	_s('ebony',               'Ebony',               1,  'epic',      'Play 750 AI games',                                       _count('games', 750)),
	_s('burl',                'Burl',                1,  'legendary', 'Win 400 AI games (diff 50+)',                             _count('wins50', 400)),
	_s('marble',              'Marble',              2,  'uncommon',  'Beat difficulty 50+',                                     _win(50)),
	_s('granite',             'Granite',             2,  'rare',      'Beat difficulty 70+',                                     _win(70)),
	_s('jade',                'Jade',                2,  'epic',      'Beat difficulty 90+',                                     _win(90)),
	_s('obsidian',            'Obsidian',            2,  'legendary', 'Beat difficulty 100',                                     _win(100)),
	_s('copper',              'Copper',              3,  'uncommon',  'Reach level 10',                                          _count('level', 10)),
	_s('bronze',              'Bronze',              3,  'rare',      'Reach level 25',                                          _count('level', 25)),
	_s('silver',              'Silver',              3,  'epic',      'Reach level 50',                                          _count('level', 50)),
	_s('gold',                'Gold',                3,  'legendary', 'Reach level 75',                                          _count('level', 75)),
	_s('platinum',            'Platinum',            3,  'legendary', 'Reach level 100',                                         _count('level', 100)),
	_s('ruby',                'Ruby',                4,  'uncommon',  '3-win streak (diff 50+)',                                 _count('streak50', 3)),
	_s('sapphire',            'Sapphire',            4,  'rare',      '5-win streak (diff 50+)',                                 _count('streak50', 5)),
	_s('emerald',             'Emerald',             4,  'epic',      '7-win streak (diff 60+)',                                 _count('streak60', 7)),
	_s('amethyst',            'Amethyst',            4,  'epic',      '10-win streak (diff 60+)',                                _count('streak60', 10)),
	_s('prismatic-brilliant', 'Prismatic Brilliant', 4,  'legendary', '15-win streak (diff 70+)',                                _count('streak70', 15)),
	_s('black-diamond',       'Black Diamond',       4,  'legendary', 'Win 25 games vs diff 90+',                                _count('wins90', 25)),
	_s('fire-diamond',        'Fire Diamond',        4,  'legendary', '3-win streak vs diff 100',                                _count('streak100', 3)),
	_s('neon-pink',           'Neon Pink',           5,  'uncommon',  'Win a Speed game (diff 30+)',                             _win(30, speed=1)),
	_s('neon-green',          'Neon Green',          5,  'rare',      'Win 15 Speed games (diff 50+)',                           _count('speed_wins50', 15)),
	_s('neon-blue',           'Neon Blue',           5,  'rare',      'Win a Speed game vs diff 80+',                            _win(80, speed=1)),
	_s('synthwave',           'Synthwave',           5,  'epic',      'Score 250+ in a Speed game and win (diff 60+)',           ((lambda c, g: g['won'] and g['speed'] and g['diff'] >= 60 and g['score'] >= 250), None)),
	_s('laser',               'Laser',               5,  'legendary', '5 Speed wins in a row (diff 80+)',                        _count('speed_streak80', 5)),
	_s('pixel',               'Pixel',               6,  'common',    'Win a Random-mode game (diff 30+)',                       _win(30, random=1)),
	_s('crt',                 'CRT',                 6,  'uncommon',  'Win 15 Random games (diff 50+)',                          _count('random_wins50', 15)),
	_s('amber-term',          'Amber Term',          6,  'rare',      'Win a Random game vs diff 80+',                           _win(80, random=1)),
	_s('handheld',            'Handheld',            6,  'epic',      '100+ point play in Random mode',                          _feat(lambda g: g['random'] and g['max_play'] >= 100)),
	_s('arcade',              'Arcade',              6,  'legendary', 'Win Random + Speed vs diff 90+',                          _win(90, random=1, speed=1)),
	_s('seafoam',             'Seafoam',             7,  'common',    '5,000 lifetime points',                                   _count('lifetime', 5000)),
	_s('coral',               'Coral',               7,  'uncommon',  '25,000 lifetime points',                                  _count('lifetime', 25000)),
	_s('deep-sea',            'Deep Sea',            7,  'rare',      '75,000 lifetime points',                                  _count('lifetime', 75000)),
	_s('pearl',               'Pearl',               7,  'epic',      '150,000 lifetime points',                                 _count('lifetime', 150000)),
	_s('abyss',               'Abyss',               7,  'legendary', '300,000 lifetime points',                                 _count('lifetime', 300000)),
	_s('moss',                'Moss',                8,  'common',    'Play your first bingo',                                   _count('bingos', 1)),
	_s('leaf',                'Leaf',                8,  'uncommon',  '15 lifetime bingos',                                      _count('bingos', 15)),
	_s('sakura',              'Sakura',              8,  'rare',      '2 bingos in one game',                                    _feat(lambda g: g['bingos'] >= 2)),
	_s('autumn',              'Autumn',              8,  'epic',      '150 lifetime bingos',                                     _count('bingos', 150)),
	_s('bamboo',              'Bamboo',              8,  'legendary', '3 bingos in one game (diff 70+)',                         _feat(lambda g: g['bingos'] >= 3 and g['diff'] >= 70)),
	_s('dawn',                'Dawn',                9,  'common',    '40+ point play',                                          _feat(lambda g: g['max_play'] >= 40)),
	_s('noon',                'Noon',                9,  'uncommon',  '60+ point play',                                          _feat(lambda g: g['max_play'] >= 60)),
	_s('sunset',              'Sunset',              9,  'rare',      '80+ point play',                                          _feat(lambda g: g['max_play'] >= 80)),
	_s('storm',               'Storm',               9,  'epic',      '120+ point play',                                         _feat(lambda g: g['max_play'] >= 120)),
	_s('aurora',              'Aurora',              9,  'legendary', '160+ point play',                                         _feat(lambda g: g['max_play'] >= 160)),
	_s('nebula',              'Nebula',              10, 'uncommon',  'Score 350+ in a game',                                    _feat(lambda g: g['score'] >= 350)),
	_s('starfield',           'Starfield',           10, 'rare',      'Score 450+ in a game',                                    _feat(lambda g: g['score'] >= 450)),
	_s('moon',                'Moon',                10, 'rare',      'Score 500+ in a game (diff 60+)',                         _feat(lambda g: g['score'] >= 500 and g['diff'] >= 60)),
	_s('mars',                'Mars',                10, 'epic',      'Score 600+ in a game',                                    _feat(lambda g: g['score'] >= 600)),
	_s('black-hole',          'Black Hole',          10, 'legendary', 'Score 700+ in a game',                                    _feat(lambda g: g['score'] >= 700)),
	_s('ember',               'Ember',               11, 'uncommon',  'Win by 100+ (diff 50+)',                                  ((lambda c, g: g['won'] and g['diff'] >= 50 and g['margin'] >= 100), None)),
	_s('snow-globe',          'Snow Globe',          11, 'rare',      'Hold the AI under 250 and win (diff 50+)',                ((lambda c, g: g['won'] and g['diff'] >= 50 and g['ai_score'] < 250), None)),
	_s('frostbite',           'Frostbite',           11, 'rare',      'Hold the AI under 300 in 25 wins (diff 60+)',             _count('under300_wins60', 25)),
	_s('cryo',                'Cryo',                11, 'rare',      'Win where the AI never scores a 30+ play (diff 70+)',     ((lambda c, g: g['won'] and g['diff'] >= 70 and g['ai_max_play'] < 30), None)),
	_s('lava',                'Lava',                11, 'epic',      'Win by 200+ (diff 70+)',                                  ((lambda c, g: g['won'] and g['diff'] >= 70 and g['margin'] >= 200), None)),
	_s('glacier',             'Glacier',             11, 'epic',      'Hold the AI under 200 and win (diff 80+)',                ((lambda c, g: g['won'] and g['diff'] >= 80 and g['ai_score'] < 200), None)),
	_s('plasma',              'Plasma',              11, 'legendary', 'Win by 300+ (diff 90+)',                                  ((lambda c, g: g['won'] and g['diff'] >= 90 and g['margin'] >= 300), None)),
	_s('sticky-note',         'Sticky Note',         12, 'common',    'Play a 7-letter word',                                    _feat(lambda g: g['longest'] >= 7)),
	_s('notebook',            'Notebook',            12, 'common',    'Play 100 words',                                          _count('words', 100)),
	_s('cardboard',           'Cardboard',           12, 'common',    'Win 1 AI game (diff 20+)',                                _count('wins20', 1)),
	_s('graph',               'Graph',               12, 'uncommon',  'Play an 8-letter word',                                   _feat(lambda g: g['longest'] >= 8)),
	_s('comic-panel',         'Comic Panel',         12, 'rare',      'Play a 10+ letter word',                                  _feat(lambda g: g['longest'] >= 10)),
	_s('origami',             'Origami',             12, 'epic',      'Form 5+ words in one play',                               _feat(lambda g: g['max_words'] >= 5)),
	_s('blueprint',           'Blueprint',           12, 'legendary', 'Play 2,000 distinct words',                               _count('distinct', 2000)),
	_s('bubblegum',           'Bubblegum',           13, 'common',    'Play a word with Z',                                      _feat(lambda g: g['z_word'])),
	_s('lemon-drop',          'Lemon Drop',          13, 'uncommon',  'Play Q without U',                                        _feat(lambda g: g['q_no_u'])),
	_s('gummy',               'Gummy',               13, 'rare',      'Play J, Q, X and Z in one game',                          _feat(lambda g: set('JQXZ') <= set(g['letters']))),
	_s('cotton-candy',        'Cotton Candy',        13, 'rare',      'Play 50 words containing J, Q, X or Z',                   _count('jqxz_words', 50)),
	_s('candy-cane',          'Candy Cane',          13, 'rare',      'Score 40+ in a play that uses Q',                         _feat(lambda g: g['q40'])),
	_s('licorice',            'Licorice',            13, 'epic',      'Score 50+ with X or Z on a triple letter both ways',      _feat(lambda g: g['xz_tl50'])),
	_s('sprinkles',           'Sprinkles',           13, 'legendary', 'Play every letter A-Z in a single game',                  _feat(lambda g: len(set(g['letters'])) >= 26)),
	_s('denim',               'Denim',               14, 'common',    'Cover 25 triple-word squares',                            _count('tw', 25)),
	_s('felt',                'Felt',                14, 'uncommon',  'Use both blanks in one game and win',                     ((lambda c, g: g['won'] and g['diff'] >= FEAT_MIN_DIFF and g['blanks'] >= 2), None)),
	_s('plaid',               'Plaid',               14, 'rare',      'Double-double (two DW in one play)',                      _feat(lambda g: g['dw2'])),
	_s('velvet',              'Velvet',              14, 'epic',      'Win where every one of your plays scored 20+ (diff 60+)', ((lambda c, g: g['won'] and g['diff'] >= 60 and g['plays'] > 0 and g['min_play'] >= 20), None)),
	_s('silk',                'Silk',                14, 'legendary', 'Triple-triple (two TW in one play)',                      _feat(lambda g: g['tw2'])),
	_s('circuit',             'Circuit',             15, 'uncommon',  'Beat Stacker',                                            _win(0, stacker=1)),
	_s('carbon',              'Carbon',              15, 'rare',      'Beat Stacker 15 times',                                   _count('stacker_wins', 15)),
	_s('hologram',            'Hologram',            15, 'epic',      '5 wins in a row vs Stacker',                              _count('stacker_streak', 5)),
	_s('matrix',              'Matrix',              15, 'legendary', 'Beat Stacker in Random + Speed',                          _win(0, stacker=1, random=1, speed=1)),
	_s('pop-art',             'Pop Art',             16, 'uncommon',  'Win after trailing by 50+ (diff 50+)',                    ((lambda c, g: g['won'] and g['diff'] >= 50 and g['max_deficit'] >= 50), None)),
	_s('watercolor',          'Watercolor',          16, 'rare',      'Win by under 10 points (diff 50+)',                       ((lambda c, g: g['won'] and g['diff'] >= 50 and g['margin'] < 10), None)),
	_s('art-deco',            'Art Deco',            16, 'rare',      'Win after trailing by 100+ (diff 50+)',                   ((lambda c, g: g['won'] and g['diff'] >= 50 and g['max_deficit'] >= 100), None)),
	_s('mosaic',              'Mosaic',              16, 'rare',      'Win 10 games after trailing by 50+ (diff 50+)',           _count('trail50_wins', 10)),
	_s('ukiyo-e-wave',        'Ukiyo-e Wave',        16, 'rare',      'Take the lead on your final play and win (diff 60+)',     ((lambda c, g: g['won'] and g['diff'] >= 60 and g['took_lead']), None)),
	_s('stained-glass',       'Stained Glass',       16, 'epic',      'Go out with a bingo on your final play and win',          ((lambda c, g: g['won'] and g['diff'] >= FEAT_MIN_DIFF and g['bingo_out']), None)),
	_s('starry-swirl',        'Starry Swirl',        16, 'legendary', 'Win after trailing by 150+ (diff 80+)',                   ((lambda c, g: g['won'] and g['diff'] >= 80 and g['max_deficit'] >= 150), None)),
	_s('ink-bloom',           'Ink Bloom',           16, 'legendary', 'Win by exactly 1 or 2 points (diff 70+)',                 ((lambda c, g: g['won'] and g['diff'] >= 70 and g['margin'] <= 2), None)),
	_s('kaleidoscope',        'Kaleidoscope',        16, 'legendary', 'Win 5 games by under 10 points (diff 60+)',               _count('close_wins60', 5)),
	_s('lava-lamp',           'Lava Lamp',           16, 'legendary', 'Win after trailing by 100+ vs diff 100',                  ((lambda c, g: g['won'] and g['diff'] >= 100 and g['max_deficit'] >= 100), None)),
	_s('ink',                 'Ink',                 17, 'common',    'Win without swapping (diff 40+)',                         ((lambda c, g: g['won'] and g['diff'] >= 40 and not g['swaps']), None)),
	_s('chalk',               'Chalk',               17, 'uncommon',  'Win without passing or swapping (diff 60+)',              ((lambda c, g: g['won'] and g['diff'] >= 60 and not g['swaps'] and not g['passes']), None)),
	_s('charcoal',            'Charcoal',            17, 'rare',      'Win 25 games without hints (diff 70+)',                   _count('nohint_wins70', 25)),
	_s('static',              'Static',              17, 'epic',      'Win without a single 2-letter word (diff 70+)',           ((lambda c, g: g['won'] and g['diff'] >= 70 and g['plays'] > 0 and not g['two_letter']), None)),
	_s('void',                'Void',                17, 'legendary', 'Win in 10 or fewer of your turns (diff 70+)',             ((lambda c, g: g['won'] and g['diff'] >= 70 and g['turns'] <= 10), None)),
	_s('champagne',           'Champagne',           18, 'uncommon',  'Play AI games on 7 different days',                       _count('days', 7)),
	_s('onyx-gold',           'Onyx & Gold',         18, 'rare',      'Play AI games 14 days in a row',                          _count('best_day_streak', 14)),
	_s('rose-gold',           'Rose Gold',           18, 'epic',      'Play AI games on 100 different days',                     _count('days', 100)),
	_s('opal',                'Opal',                18, 'epic',      'Win vs diff 70+ every day for 14 days',                   _count('best_w70_streak', 14)),
	_s('prism',               'Prism',               18, 'legendary', 'Unlock 85 styles',                                        _count('count', 85)),
	_s('forge',               'Forge',               19, 'secret',    'Average 30+ pts/play over 50 straight games (diff 60+)',  ((lambda c, g: len(c.get('window', [])) >= WINDOW_GAMES and sum(p for p, _ in c['window']) >= 30 * max(1, sum(n for _, n in c['window']))), None)),
	_s('gold-ink',            'Gold Ink',            19, 'secret',    'Play three 8+ letter words in one game',                  _feat(lambda g: g['eight_plus'] >= 3)),
	_s('bingo-king',          'Bingo King',          19, 'secret',    '3 bingos in one game vs diff 100',                        _feat(lambda g: g['bingos'] >= 3 and g['diff'] >= 100)),
	_s('halo',                'Halo',                19, 'secret',    'Beat diff 100 by 200+',                                   ((lambda c, g: g['won'] and g['diff'] >= 100 and g['margin'] >= 200), None)),
	_s('chrome',              'Chrome',              19, 'secret',    'Beat diff 100 without swapping, passing or hints',        ((lambda c, g: g['won'] and g['diff'] >= 100 and not (g['swaps'] or g['passes'] or g['hints'])), None)),
	_s('royal',               'Royal',               19, 'secret',    '10-win streak vs diff 100',                               _count('streak100', 10)),
	_s('inferno',             'Inferno',             19, 'secret',    'Score 600+ vs diff 100',                                  _feat(lambda g: g['score'] >= 600 and g['diff'] >= 100)),
	_s('gilded-marble',       'Gilded Marble',       19, 'secret',    'Beat diff 100 fifty times',                               _count('wins100', 50)),
	_s('galaxy-crown',        'Galaxy Crown',        19, 'secret',    '150+ point play vs diff 100',                             _feat(lambda g: g['max_play'] >= 150 and g['diff'] >= 100)),
	_s('infinity',            'Infinity',            19, 'secret',    'Unlock all 112 other styles',                             _count('count', 112)),
]
STYLE_IDS = {s[0] for s in STYLES}
assert len(STYLES) == len(STYLE_IDS) == 113


def init_db():
	'''Create the progress tables.'''

	with db_connect() as c:
		c.execute('CREATE TABLE IF NOT EXISTS user_progress (user_id INTEGER PRIMARY KEY, xp INTEGER NOT NULL DEFAULT 0, style TEXT NOT NULL DEFAULT \'classic\', data TEXT NOT NULL DEFAULT \'{}\')')
		c.execute('CREATE TABLE IF NOT EXISTS user_ai_words (user_id INTEGER NOT NULL, word TEXT NOT NULL, PRIMARY KEY (user_id, word))')
		c.commit()


def _load(c, uid: int) -> tuple:
	row = c.execute('SELECT xp, style, data FROM user_progress WHERE user_id=?', (uid,)).fetchone()
	if not row:
		return 0, DEFAULT_STYLE, {}
	return row['xp'], row['style'], json.loads(row['data'] or '{}')


def profile(uid) -> tuple:
	'''(style, level) shown for a player in a room. Players without an account get the classic look and no level.'''

	if not uid:
		return DEFAULT_STYLE, None
	with db_connect() as c:
		xp, style, _data = _load(c, uid)
	return style, level_for(xp)


def track(game: dict, slot: int) -> dict:
	'''Per-game tracking for one seat, created on first use so rooms saved before this existed keep working.'''

	t = game.setdefault('track', [])
	while len(t) <= slot:
		t.append({})
	return t[slot]


def note_action(game: dict, slot: int, kind: str):
	'''Count a swap, pass or hint for a seat.'''

	t = track(game, slot)
	t[kind] = t.get(kind, 0) + 1


def note_play(game: dict, slot: int, placements: list, words: list, score: int, valid: bool):
	'''Record what a play did. words is [(word, [(r, c, letter, is_new), ...])], scores are already updated.'''

	t = track(game, slot)
	bonus = game['bonus_board']
	scores = game['scores']
	n = game.get('max_players', 2)
	others_before = max((scores[i] for i in range(n) if i != slot), default=0)
	t['took_lead'] = scores[slot] - score <= others_before < scores[slot]
	t['plays'] = t.get('plays', 0) + 1
	t['max_play'] = max(t.get('max_play', 0), score)
	t['min_play'] = score if t.get('min_play') is None else min(t['min_play'], score)
	t['last_bingo'] = len(placements) == 7
	if len(placements) == 7:
		t['bingos'] = t.get('bingos', 0) + 1
	if score >= 50:
		t['plays50'] = t.get('plays50', 0) + 1
	squares = [bonus[p['row']][p['col']] for p in placements]
	t['tw'] = t.get('tw', 0) + squares.count('TW')
	if squares.count('DW') + squares.count('ST') >= 2:
		t['dw2'] = True
	if squares.count('TW') >= 2:
		t['tw2'] = True
	t['blanks'] = t.get('blanks', 0) + sum(1 for p in placements if p.get('blank'))
	t['letters'] = ''.join(sorted(set(t.get('letters', '')) | {p['letter'] for p in placements}))
	if 'Q' in {p['letter'] for p in placements} and score >= 40:
		t['q40'] = True
	if score >= 50:
		for p in placements:
			if p['letter'] in 'XZ' and not p.get('blank') and bonus[p['row']][p['col']] == 'TL' and sum(1 for _w, cells in words if any(r == p['row'] and c == p['col'] for r, c, _l, _new in cells)) >= 2:
				t['xz_tl50'] = True
	for i in range(n): # largest deficit each seat has faced so far
		ti = track(game, i)
		ti['max_deficit'] = max(ti.get('max_deficit', 0), max((scores[j] for j in range(n) if j != i), default=0) - scores[i])
	if valid:
		t['max_words'] = max(t.get('max_words', 0), len(words))
		t['words'] = t.get('words', []) + [w.upper() for w, _cells in words]


def _summary(game: dict, human: int, ai: int, reason: str, won: bool) -> dict:
	'''This game's numbers for the human seat.'''

	t = track(game, human)
	words = t.get('words', [])
	raw_diff = game.get('ai_difficulty', 50)
	stacker = game.get('ai_style') == 'stacker'
	score, ai_score = game['scores'][human], game['scores'][ai]
	return {
		'won': won, 'raw_diff': raw_diff, 'stacker': stacker, 'diff': STACKER_DIFF if stacker else raw_diff,
		'speed': bool(game.get('speed_mode')), 'random': game.get('mode') == 'random',
		'score': score, 'ai_score': ai_score, 'margin': score - ai_score,
		'plays': t.get('plays', 0), 'bingos': t.get('bingos', 0), 'plays50': t.get('plays50', 0),
		'max_play': t.get('max_play', 0), 'min_play': t.get('min_play') or 0, 'ai_max_play': track(game, ai).get('max_play', 0),
		'max_words': t.get('max_words', 0), 'longest': max((len(w) for w in words), default=0), 'eight_plus': len({w for w in words if len(w) >= 8}),
		'letters': t.get('letters', ''), 'z_word': any('Z' in w for w in words), 'q_no_u': any('Q' in w and 'U' not in w for w in words),
		'q40': t.get('q40', False), 'xz_tl50': t.get('xz_tl50', False), 'tw': t.get('tw', 0), 'dw2': t.get('dw2', False), 'tw2': t.get('tw2', False),
		'blanks': t.get('blanks', 0), 'two_letter': any(len(w) == 2 for w in words), 'swaps': t.get('swaps', 0), 'passes': t.get('passes', 0), 'hints': t.get('hints', 0),
		'max_deficit': t.get('max_deficit', 0), 'took_lead': t.get('took_lead', False), 'turns': game['moves'][human],
		'bingo_out': reason == 'tiles_out' and not game['racks'][human] and t.get('last_bingo', False),
		'words': words,
	}


def _bump_days(c: dict, today: str, yesterday: str, key_last: str, key_streak: str, key_best: str):
	if c.get(key_last) == today:
		return
	c[key_streak] = c.get(key_streak, 0) + 1 if c.get(key_last) == yesterday else 1
	c[key_best] = max(c.get(key_best, 0), c[key_streak])
	c[key_last] = today


def record_game(room: dict, reason: str) -> dict:
	'''Apply a finished AI game to the human player's level and unlocks. Returns what changed, or None when the game does not count.'''

	game = room['game']
	if not game.get('vs_computer') or game.get('max_players', 2) != 2:
		return None
	ai = game.get('ai_slot')
	if ai is None:
		return None
	human = 1 - ai
	uid = (room.get('user_ids') or [None, None])[human] or (room.get('quit_user_ids') or {}).get(str(human))
	if not uid or game['moves'][human] < 1:
		return None
	if reason not in FINISHED and reason not in LOSS_REASONS:
		return None # the reaper ended it, so it never finished
	winner = (game.get('final_summary') or {}).get('winner')
	draw = game.get('game_result') == 'draw' or winner is None
	won = not draw and winner == human
	g = _summary(game, human, ai, reason, won)
	now = time.gmtime()
	today = time.strftime('%Y-%m-%d', now)
	yesterday = time.strftime('%Y-%m-%d', time.gmtime(time.time() - 86400))
	with DB_LOCK:
		with db_connect() as conn:
			xp, style, c = _load(conn, uid)
			level_before = level_for(xp)
			if not won and not draw: # any loss ends every win streak
				for k in ('streak50', 'streak60', 'streak70', 'streak100', 'speed_streak80', 'stacker_streak'):
					c[k] = 0
			gained = 0
			unlocked = []
			if reason in FINISHED:
				gained = game_xp(g)
				xp += gained
				d = g['diff']
				c['games'] = c.get('games', 0) + 1
				c['days'] = c.get('days', 0) + (0 if c.get('last_day') == today else 1)
				_bump_days(c, today, yesterday, 'last_day', 'day_streak', 'best_day_streak')
				if won:
					for th in (20, 40, 50, 90, 100):
						if d >= th:
							c[f'wins{th}'] = c.get(f'wins{th}', 0) + 1
					for th in (50, 60, 70, 100):
						if d >= th:
							c[f'streak{th}'] = c.get(f'streak{th}', 0) + 1
					if g['speed'] and d >= 50:
						c['speed_wins50'] = c.get('speed_wins50', 0) + 1
					if g['speed'] and d >= 80:
						c['speed_streak80'] = c.get('speed_streak80', 0) + 1
					if g['random'] and d >= 50:
						c['random_wins50'] = c.get('random_wins50', 0) + 1
					if g['stacker']:
						c['stacker_wins'] = c.get('stacker_wins', 0) + 1
						c['stacker_streak'] = c.get('stacker_streak', 0) + 1
					if d >= 60 and g['ai_score'] < 300:
						c['under300_wins60'] = c.get('under300_wins60', 0) + 1
					if d >= 50 and g['max_deficit'] >= 50:
						c['trail50_wins'] = c.get('trail50_wins', 0) + 1
					if d >= 60 and g['margin'] < 10:
						c['close_wins60'] = c.get('close_wins60', 0) + 1
					if d >= 70 and not g['hints']:
						c['nohint_wins70'] = c.get('nohint_wins70', 0) + 1
					if d >= 70:
						_bump_days(c, today, yesterday, 'w70_last_day', 'w70_streak', 'best_w70_streak')
				if d >= FEAT_MIN_DIFF:
					c['lifetime'] = c.get('lifetime', 0) + max(0, g['score'])
					c['bingos'] = c.get('bingos', 0) + g['bingos']
					c['tw'] = c.get('tw', 0) + g['tw']
					c['words'] = c.get('words', 0) + len(g['words'])
					c['jqxz_words'] = c.get('jqxz_words', 0) + sum(1 for w in g['words'] if set(w) & set('JQXZ'))
					conn.executemany('INSERT OR IGNORE INTO user_ai_words(user_id, word) VALUES(?, ?)', [(uid, w) for w in set(g['words'])])
				if d >= 60:
					c['window'] = (c.get('window', []) + [[max(0, g['score']), g['plays']]])[-WINDOW_GAMES:]
				c['distinct'] = conn.execute('SELECT COUNT(*) FROM user_ai_words WHERE user_id=?', (uid,)).fetchone()[0]
				c['level'] = level_for(xp)
				have = set(c.get('unlocked', [DEFAULT_STYLE])) | {DEFAULT_STYLE}
				changed = True
				while changed: # a new unlock can complete Prism or Infinity
					changed = False
					c['count'] = len(have)
					for sid, _name, _fam, _tier, _text, cond, _prog in STYLES:
						if sid not in have and cond(c, g):
							have.add(sid)
							unlocked.append(sid)
							changed = True
				c['unlocked'] = sorted(have)
			conn.execute('INSERT INTO user_progress(user_id, xp, style, data) VALUES(?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET xp=excluded.xp, data=excluded.data', (uid, xp, style, json.dumps(c)))
			conn.commit()
	level = level_for(xp)
	room.setdefault('levels', [None] * game.get('max_players', 2))[human] = level
	names = {sid: name for sid, name, *_rest in STYLES}
	result = {'xp': gained, 'total_xp': xp, 'level_before': level_before, 'level': level, 'next_level_xp': xp_for(level + 1), 'unlocked': unlocked, 'unlocked_names': [names[sid] for sid in unlocked]}
	game.setdefault('progress', {})[str(human)] = result
	return result


def player_view(uid: int) -> dict:
	'''Level, XP and every style with its unlock state and progress, for the profile panel. Locked secret styles hide their name and condition.'''

	with db_connect() as conn:
		xp, style, c = _load(conn, uid)
	level = level_for(xp)
	c['level'] = level
	have = set(c.get('unlocked', [])) | {DEFAULT_STYLE}
	c['count'] = len(have)
	styles = []
	for sid, name, fam, tier, text, _cond, prog in STYLES:
		got = sid in have
		hidden = tier == 'secret' and not got
		entry = {'id': sid, 'family': FAMILIES[fam], 'tier': tier, 'unlocked': got, 'name': '???' if hidden else name, 'unlock': '???' if hidden else text}
		if prog and not got and not hidden:
			cur, target = prog(c)
			entry['progress'] = [min(cur, target), target]
		styles.append(entry)
	return {'xp': xp, 'level': level, 'level_xp': xp_for(level), 'next_level_xp': xp_for(level + 1), 'style': style if style in have else DEFAULT_STYLE, 'unlocked_count': len(have), 'total': len(STYLES), 'styles': styles}


def set_style(uid: int, sid: str) -> bool:
	'''Choose an unlocked style. Returns False when the style is unknown or still locked.'''

	if sid not in STYLE_IDS:
		return False
	with DB_LOCK:
		with db_connect() as conn:
			xp, _style, c = _load(conn, uid)
			if sid != DEFAULT_STYLE and sid not in c.get('unlocked', []):
				return False
			conn.execute('INSERT INTO user_progress(user_id, xp, style, data) VALUES(?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET style=excluded.style', (uid, xp, sid, json.dumps(c)))
			conn.commit()
	return True


def top_levels(limit: int) -> list:
	'''Highest XP players: [(user_id, xp, level)].'''

	with db_connect() as conn:
		rows = conn.execute('SELECT user_id, xp FROM user_progress WHERE xp > 0 ORDER BY xp DESC LIMIT ?', (limit,)).fetchall()
	return [(r['user_id'], r['xp'], level_for(r['xp'])) for r in rows]
