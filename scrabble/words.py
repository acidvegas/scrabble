# scrabble - Developed by acidvegas in Python (https://github.com/acidvegas)
# words.py

import os
import re
import urllib.request

import move_gen


DICT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'words', 'nwl2023.txt')
DICT_URL = 'https://raw.githubusercontent.com/scrabblewords/scrabblewords/main/words/North-American/NWL2023.txt'

POS_NAMES = {
	'n': 'noun', 'v': 'verb', 'adj': 'adjective', 'adv': 'adverb',
	'interj': 'interjection', 'conj': 'conjunction', 'prep': 'preposition',
	'pron': 'pronoun', 'article': 'article',
}

_BRACKET_RE = re.compile(r'\s*\[([a-z]+)(?:\s+[^\]]+)?\]\s*')
_ANG_REF_RE = re.compile(r'^<([a-zA-Z]+)=[a-z]+>$')
_CURLY_REF_RE = re.compile(r'^(?:[a-z]+\s+)?\{([a-zA-Z]+)=[a-z]+\}$')
_INLINE_REF_RE = re.compile(r'[{<]([a-zA-Z]+)=[a-z]+[}>]')


def _clean_definition(text):
	return _INLINE_REF_RE.sub(lambda m: m.group(1).lower(), text)


def load_dictionary():
	if not os.path.exists(DICT_PATH):
		print(f'Downloading NWL2023 word list to {DICT_PATH}...')
		os.makedirs(os.path.dirname(DICT_PATH), exist_ok=True)
		urllib.request.urlretrieve(DICT_URL, DICT_PATH)
	words = set()
	raw = {}
	with open(DICT_PATH) as f:
		for line in f:
			line = line.rstrip('\n')
			if not line or line.startswith('#'):
				continue
			parts = line.split(' ', 1)
			word = parts[0].upper()
			words.add(word)
			raw[word] = parts[1] if len(parts) > 1 else ''
	defs = {}
	for word, rest in raw.items():
		body = rest.split(' : ', 1)[0]
		matches = list(_BRACKET_RE.finditer(body))
		pos = ''
		if matches:
			pos = matches[-1].group(1)
			body = body[:matches[-1].start()].strip()
		ref = None
		ang = _ANG_REF_RE.match(body)
		cur = _CURLY_REF_RE.match(body)
		if ang:
			ref = ang.group(1).upper()
			body = ''
		elif cur:
			ref = cur.group(1).upper()
			body = ''
		defs[word] = {'ref': ref, 'pos': pos, 'definition': body}
	for word, entry in defs.items():
		if not entry['ref']:
			continue
		seen = {word}
		cur_word = entry['ref']
		while cur_word and cur_word in defs and cur_word not in seen:
			target = defs[cur_word]
			if not target['ref']:
				if target['definition']:
					entry['definition'] = target['definition']
				if not entry['pos']:
					entry['pos'] = target['pos']
				break
			seen.add(cur_word)
			cur_word = target['ref']
	print(f'Loaded {len(words)} dictionary words (NWL2023) with {sum(1 for e in defs.values() if e["definition"])} definitions')
	return words, defs


DICTIONARY, DEFINITIONS = load_dictionary()
WORD_TRIE = move_gen.build_trie(DICTIONARY)
print(f'Built move-gen trie ({len(DICTIONARY)} words)')


def check_dictionary(word):
	return word.upper() in DICTIONARY


def fetch_definition(word):
	entry = DEFINITIONS.get(word.upper())
	if not entry:
		return None
	definition = entry.get('definition') or ''
	if not definition and entry.get('ref'):
		definition = f"see {entry['ref']}"
	if not definition:
		return None
	definition = _clean_definition(definition)
	pos_code = entry.get('pos') or ''
	return {
		'partOfSpeech': POS_NAMES.get(pos_code, pos_code),
		'definition': definition,
	}
