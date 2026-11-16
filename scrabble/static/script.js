// scrabble - Developed by acidvegas in Python (https://github.com/acidvegas)
// script.js

window.ARCADE_PUBLIC_URL = document.currentScript.dataset.arcadePublicUrl;

const LETTER_VALUES = {
	A:1,B:3,C:3,D:2,E:1,F:4,G:2,H:4,I:1,J:8,
	K:5,L:1,M:3,N:1,O:1,P:3,Q:10,R:1,S:1,T:1,
	U:1,V:4,W:4,X:8,Y:4,Z:10,_:0
};

// Carry over anything saved under the old "scabble_" key names
try {
	for (const key of Object.keys(localStorage)) {
		if (!key.startsWith('scabble_')) continue;
		const newKey = 'scrabble_' + key.slice('scabble_'.length);
		if (localStorage.getItem(newKey) === null) localStorage.setItem(newKey, localStorage.getItem(key));
		localStorage.removeItem(key);
	}
} catch (e) {}

const state = {
	board: [],
	blanks: [],
	bonus: [],
	rack: [],
	scores: [0, 0],
	current: 0,
	bagCount: 100,
	firstMove: true,
	pending: [],
	swapMode: false,
	swapSelection: new Set(),
	lastPlayer: null,
	lastWords: [],
	lastScore: 0,
	lastDefinitions: [],
	preview: null,
	roomId: localStorage.getItem('scrabble_room_id') || null,
	playerToken: localStorage.getItem('scrabble_player_token') || null,
	playerName: localStorage.getItem('scrabble_player_name') || '',
	authToken: localStorage.getItem('scrabble_auth_token') || null,
	authUsername: localStorage.getItem('scrabble_auth_username') || null,
	authUserId: null,
	userIds: [],
	mySlot: null,
	hasOpponent: false,
	names: ['P1', 'P2'],
	lastAction: null,
	chat: [],
	chatSeq: 0,
	lastSeenChatId: 0,
	lastNotifiedChatId: 0,
	unreadChat: 0,
	chatOpen: false,
	finalSummary: null,
	endgameShown: false,
	hintCells: null,
	hintTokens: 0,
};

function cleanName(raw) {
	return (raw || '').replace(/[^A-Za-z]/g, '').slice(0, 9).toUpperCase();
}

function saveName(name) {
	state.playerName = name;
	localStorage.setItem('scrabble_player_name', name);
}

function loadRoomsMap() {
	try { return JSON.parse(localStorage.getItem('scrabble_rooms') || '{}'); }
	catch (e) { return {}; }
}
function saveRoomsMap(m) {
	try { localStorage.setItem('scrabble_rooms', JSON.stringify(m)); } catch (e) {}
}
function setRoomCred(rid, token) {
	const m = loadRoomsMap();
	m[rid] = { token, lastSeen: Date.now() };
	saveRoomsMap(m);
}
function delRoomCred(rid) {
	const m = loadRoomsMap();
	if (m[rid]) { delete m[rid]; saveRoomsMap(m); }
}
function getRoomCred(rid) {
	return loadRoomsMap()[rid] || null;
}
function migrateRoomsMap() {
	const m = loadRoomsMap();
	const oldRid = localStorage.getItem('scrabble_room_id');
	const oldTok = localStorage.getItem('scrabble_player_token');
	if (oldRid && oldTok && !m[oldRid]) {
		m[oldRid] = { token: oldTok, lastSeen: Date.now() };
		saveRoomsMap(m);
	}
}

async function syncActiveRoomsFromAccount() {
	if (!state.authToken) return;
	try {
		const r = await fetch('/auth/active_rooms', { headers: { 'X-Auth-Token': state.authToken } });
		const d = await r.json();
		if (!Array.isArray(d.rooms)) return;
		// Add discovered rooms with placeholder tokens; /join_room will reissue a real token on entry.
		const m = loadRoomsMap();
		let changed = false;
		for (const entry of d.rooms) {
			if (!m[entry.room_id]) {
				m[entry.room_id] = { token: '', lastSeen: Date.now(), needsRejoin: true };
				changed = true;
			}
		}
		if (changed) {
			saveRoomsMap(m);
			renderActiveRooms();
		}
	} catch (e) {}
}

function chatSeenKey() {
	return state.roomId ? `scrabble_last_chat_${state.roomId}` : null;
}

function loadChatSeen() {
	const key = chatSeenKey();
	const val = key ? parseInt(localStorage.getItem(key) || '0', 10) : 0;
	state.lastSeenChatId = val;
	state.lastNotifiedChatId = val;
	state.unreadChat = 0;
}

function saveCreds(roomId, token) {
	state.roomId = roomId;
	state.playerToken = token;
	localStorage.setItem('scrabble_room_id', roomId);
	localStorage.setItem('scrabble_player_token', token);
	setRoomCred(roomId, token);
	loadChatSeen();
}

function clearCreds() {
	if (state.roomId) delRoomCred(state.roomId);
	state.roomId = null;
	state.playerToken = null;
	localStorage.removeItem('scrabble_room_id');
	localStorage.removeItem('scrabble_player_token');
	if (window.location.pathname !== '/') {
		history.replaceState(null, '', '/');
	}
}

function leaveActiveRoomKeepCreds() {
	// Switch back to the landing page without forgetting the active room.
	state.roomId = null;
	state.playerToken = null;
	localStorage.removeItem('scrabble_room_id');
	localStorage.removeItem('scrabble_player_token');
	history.replaceState(null, '', '/');
	stopRealtime();
	showLanding();
}

async function switchToRoom(rid) {
	const cred = getRoomCred(rid);
	if (!cred) return false;
	// Fresh device: no token yet, but the account is a member — rejoin to get a real token.
	if (!cred.token || cred.needsRejoin) {
		if (!state.authToken) { delRoomCred(rid); return false; }
		try {
			const body = { room_id: rid, name: state.playerName, auth_token: state.authToken };
			const r = await fetch('/join_room', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(body),
			});
			const d = await r.json();
			if (!r.ok) { delRoomCred(rid); return false; }
			saveCreds(d.room_id, d.player_token);
		} catch (e) {
			return false;
		}
	} else {
		state.roomId = rid;
		state.playerToken = cred.token;
		localStorage.setItem('scrabble_room_id', rid);
		localStorage.setItem('scrabble_player_token', cred.token);
	}
	history.replaceState(null, '', '/' + rid);
	loadChatSeen();
	const ok = await loadState();
	if (!ok) {
		delRoomCred(rid);
		return false;
	}
	hideAllRoomOverlays();
	startRealtime();
	return true;
}

function authParams() {
	return { room_id: state.roomId, player_token: state.playerToken };
}

function authQuery() {
	return `room_id=${encodeURIComponent(state.roomId)}&player_token=${encodeURIComponent(state.playerToken)}`;
}

function notifyEnabled() {
	return 'Notification' in window && Notification.permission === 'granted' && localStorage.getItem('scrabble_notify') === '1';
}

async function requestNotifyPermission() {
	if (!('Notification' in window)) return false;
	if (Notification.permission === 'granted') {
		localStorage.setItem('scrabble_notify', '1');
		return true;
	}
	if (Notification.permission === 'denied') return false;
	const p = await Notification.requestPermission();
	if (p === 'granted') {
		localStorage.setItem('scrabble_notify', '1');
		return true;
	}
	return false;
}

function fireNotification(title, body) {
	if (!notifyEnabled()) return;
	if (document.visibilityState === 'visible' && document.hasFocus()) return;
	try { new Notification(title, { body, tag: 'scrabble-' + state.roomId }); } catch (e) {}
}

let audioCtx = null;
function getAudio() {
	if (!audioCtx) {
		try { audioCtx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) {}
	}
	if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
	return audioCtx;
}

function soundsEnabled() {
	return localStorage.getItem('scrabble_sound') !== '0';
}

function playTone(freq, duration, type, vol, delay) {
	if (!soundsEnabled()) return;
	const ctx = getAudio();
	if (!ctx) return;
	const t0 = ctx.currentTime + (delay || 0);
	const osc = ctx.createOscillator();
	const gain = ctx.createGain();
	osc.type = type || 'sine';
	osc.frequency.setValueAtTime(freq, t0);
	gain.gain.setValueAtTime(vol || 0.12, t0);
	gain.gain.exponentialRampToValueAtTime(0.0001, t0 + duration);
	osc.connect(gain).connect(ctx.destination);
	osc.start(t0);
	osc.stop(t0 + duration);
}

function soundTilePlace() { playTone(720, 0.06, 'square', 0.08); }
function soundRecall() {
	playTone(500, 0.08, 'triangle', 0.1, 0);
	playTone(380, 0.1, 'triangle', 0.1, 0.06);
}
function soundPlayWord() {
	playTone(523, 0.1, 'sine', 0.13, 0);
	playTone(659, 0.1, 'sine', 0.13, 0.09);
	playTone(784, 0.18, 'sine', 0.15, 0.18);
}
function soundBingo() {
	[523, 659, 784, 1047, 1319].forEach((f, i) => playTone(f, 0.14, 'sine', 0.16, i * 0.08));
	playTone(1568, 0.45, 'sine', 0.2, 0.5);
	playTone(1047, 0.45, 'sine', 0.14, 0.5);
}
function soundOpponentPlay() {
	playTone(330, 0.1, 'triangle', 0.08, 0);
	playTone(415, 0.1, 'triangle', 0.08, 0.08);
	playTone(494, 0.18, 'triangle', 0.1, 0.16);
}
function soundChatPing() {
	playTone(880, 0.07, 'sine', 0.1, 0);
	playTone(1175, 0.1, 'sine', 0.1, 0.05);
}
function soundEndGame() {
	[659, 784, 1047, 1568].forEach((f, i) => playTone(f, 0.2, 'sine', 0.15, i * 0.12));
}

function describeLastAction() {
	if (!state.lastAction || state.lastPlayer === null) return '';
	const name = state.names[state.lastPlayer] || `P${state.lastPlayer + 1}`;
	if (state.lastAction === 'play' && state.lastWords.length > 0) {
		return `${name} played ${state.lastWords.join(', ')} (+${state.lastScore})`;
	}
	if (state.lastAction === 'swap') return `${name} swapped tiles`;
	if (state.lastAction === 'pass') return `${name} passed their turn`;
	if (state.lastAction === 'challenge_won') {
		const bad = (state.lastWords || []).join(', ');
		return `${name} challenged — invalid: ${bad}`;
	}
	if (state.lastAction === 'challenge_lost') return `${name} challenged and lost their turn`;
	return '';
}

let validateSeq = 0;
let nextRackId = 0;

function makeRackItems(letters) {
	return letters.map(letter => ({ id: nextRackId++, letter }));
}

function mergeRack(prev, serverLetters) {
	if (!prev || prev.length === 0) return makeRackItems(serverLetters);
	const serverCounts = {};
	for (const l of serverLetters) serverCounts[l] = (serverCounts[l] || 0) + 1;
	const result = [];
	for (const item of prev) {
		if (serverCounts[item.letter] > 0) {
			result.push(item);
			serverCounts[item.letter] -= 1;
		}
	}
	for (const letter of Object.keys(serverCounts)) {
		while (serverCounts[letter] > 0) {
			result.push({ id: nextRackId++, letter });
			serverCounts[letter] -= 1;
		}
	}
	return result;
}

let drag = null;
let panSession = null;
let lastZoomTapAt = 0;
const ZOOM_SCALE = 15 / 9;
const zoomState = { active: false, tx: 0, ty: 0 };
const pinchPointers = new Map();
let pinchActive = false;
let pinchInitialDist = 0;

const boardEl = document.getElementById('board');
const rackEl = document.getElementById('rack');
const messageBar = document.getElementById('message-bar');
const blankOverlay = document.getElementById('blank-overlay');
const blankLetters = document.getElementById('blank-letters');
const lastBox = document.getElementById('last-box');
const lastSummary = document.getElementById('last-summary');
const defOverlay = document.getElementById('def-overlay');
const defList = document.getElementById('def-list');
const srLive = document.getElementById('sr-live');

function announce(text) {
	if (!srLive || !text) return;
	srLive.textContent = '';
	setTimeout(() => { srLive.textContent = text; }, 50);
}

const COL_NAMES = 'ABCDEFGHIJKLMNO';
const BONUS_NAMES = { TW: 'triple word', DW: 'double word', TL: 'triple letter', DL: 'double letter', ST: 'center star', XX: 'blocked' };

function cellLabel(r, c, cell, pending, oppPending) {
	const parts = [`${COL_NAMES[c]}${r + 1}`];
	const tile = pending || oppPending;
	const placed = state.board[r][c];
	if (tile) {
		parts.push(`${tile.blank ? 'blank as ' : 'letter '}${tile.letter}`);
		parts.push(oppPending ? `${state.names[oppPending.owner] || `P${oppPending.owner + 1}`} pending` : 'your pending tile');
	} else if (placed) {
		parts.push(`${state.blanks[r][c] ? 'blank as ' : 'letter '}${placed}`);
	}
	const bonus = state.bonus[r] ? state.bonus[r][c] : '';
	if (BONUS_NAMES[bonus]) parts.push(BONUS_NAMES[bonus]);
	if (cell.classList.contains('last-play')) parts.push('last play');
	if (cell.classList.contains('hint-cell')) parts.push('hint');
	return parts.join(', ');
}

function pickBubbleCorner(cellsList, isOccupied) {
	if (!cellsList || cellsList.length === 0) return null;
	let minR = 15, maxR = -1, minC = 15, maxC = -1;
	for (const c of cellsList) {
		if (c.row < minR) minR = c.row;
		if (c.row > maxR) maxR = c.row;
		if (c.col < minC) minC = c.col;
		if (c.col > maxC) maxC = c.col;
	}
	const positions = [
		{ isBottomSide: false, isRightSide: false, pref: 0 },
		{ isBottomSide: false, isRightSide: true,  pref: 1 },
		{ isBottomSide: true,  isRightSide: false, pref: 2 },
		{ isBottomSide: true,  isRightSide: true,  pref: 3 },
	];
	let best = null;
	let bestScore = Infinity;
	for (const cell of cellsList) {
		const isBoundingCorner = (cell.row === minR || cell.row === maxR) && (cell.col === minC || cell.col === maxC);
		for (const pos of positions) {
			const dr = pos.isBottomSide ? -1 : 1;
			const dc = pos.isRightSide ? -1 : 1;
			let s = 0;
			if (isOccupied(cell.row + dr, cell.col)) s += 3;
			if (isOccupied(cell.row + dr, cell.col + dc)) s += 1;
			if (isOccupied(cell.row, cell.col + dc)) s += 0.5;
			if (cell.row + dr < 0 || cell.row + dr >= 15) s += 5;
			if (cell.col + dc < 0 || cell.col + dc >= 15) s += 2;
			if (!isBoundingCorner) s += 0.4;
			s += pos.pref * 0.01;
			if (s < bestScore) {
				bestScore = s;
				best = { row: cell.row, col: cell.col, isBottomSide: pos.isBottomSide, isRightSide: pos.isRightSide };
			}
		}
	}
	return best;
}

function buildBoard() {
	boardEl.innerHTML = '';
	for (let r = 0; r < 15; r++) {
		for (let c = 0; c < 15; c++) {
			const cell = document.createElement('div');
			cell.className = 'cell';
			cell.dataset.r = r;
			cell.dataset.c = c;
			cell.setAttribute('role', 'img');
			cell.addEventListener('pointerdown', onCellPointerDown);
			boardEl.appendChild(cell);
		}
	}
}

function renderBoard() {
	boardEl.querySelectorAll('.word-outline, .word-outline-edge').forEach(o => o.remove());
	const cells = boardEl.querySelectorAll('.cell');
	if (cells.length === 0 || !state.bonus || !state.bonus.length) return;
	const pendingMap = {};
	for (const p of state.pending) {
		pendingMap[`${p.row},${p.col}`] = p;
	}
	const oppPendingMap = {};
	if (state.transparentMode && state.pendingViews && state.current !== state.mySlot) {
		for (const [slotStr, placements] of Object.entries(state.pendingViews)) {
			const slot = parseInt(slotStr, 10);
			if (slot === state.mySlot) continue;
			for (const p of placements) {
				oppPendingMap[`${p.row},${p.col}`] = { ...p, owner: slot };
			}
		}
	}
	for (let r = 0; r < 15; r++) {
		for (let c = 0; c < 15; c++) {
			const cell = cells[r * 15 + c];
			const bonus = state.bonus[r] ? state.bonus[r][c] : '';
			const key = `${r},${c}`;
			const pending = pendingMap[key];
			const placed = state.board[r][c];
			cell.className = 'cell';
			cell.innerHTML = '';
			cell.removeAttribute('data-bonus');

			if (pending) {
				cell.classList.add('has-tile', 'pending');
				if (state.mySlot !== null) cell.classList.add('owner-' + state.mySlot);
				renderTileInto(cell, pending.letter, pending.blank);
			} else if (oppPendingMap[key]) {
				const op = oppPendingMap[key];
				cell.classList.add('has-tile', 'pending', 'opp-pending');
				cell.classList.add('owner-' + op.owner);
				renderTileInto(cell, op.letter, op.blank);
			} else if (placed) {
				cell.classList.add('has-tile');
				const owner = state.tileOwners && state.tileOwners[r] ? state.tileOwners[r][c] : null;
				if (owner != null) cell.classList.add('owner-' + owner);
				renderTileInto(cell, placed, state.blanks[r][c]);
			} else if (bonus) {
				cell.classList.add(bonus);
				if (bonus !== 'ST') cell.dataset.bonus = bonus;
			}
			if (cell.classList.contains('has-tile') && bonus && bonus !== 'ST' && bonus !== 'XX') {
				cell.classList.add('on-bonus', 'on-' + bonus);
			}
		}
	}

	const lastPlaySet = new Set();
	if (state.hintCells && state.hintCells.length > 0) {
		for (const h of state.hintCells) {
			const cell = cells[h.row * 15 + h.col];
			if (cell && !cell.classList.contains('has-tile')) cell.classList.add('hint-cell');
		}
	}

	if (state.lastPlacements && state.lastPlacements.length > 0) {
		for (const lp of state.lastPlacements) {
			lastPlaySet.add(`${lp.row},${lp.col}`);
		}
		for (const lp of state.lastPlacements) {
			const cell = cells[lp.row * 15 + lp.col];
			if (cell && cell.classList.contains('has-tile')) cell.classList.add('last-play');
		}
		for (const lp of state.lastPlacements) {
			const cell = cells[lp.row * 15 + lp.col];
			if (!cell) continue;
			if (lastPlaySet.has(`${lp.row},${lp.col + 1}`)) cell.classList.add('lp-connect-right');
			if (lastPlaySet.has(`${lp.row},${lp.col - 1}`)) cell.classList.add('lp-connect-left');
			if (lastPlaySet.has(`${lp.row + 1},${lp.col}`)) cell.classList.add('lp-connect-down');
			if (lastPlaySet.has(`${lp.row - 1},${lp.col}`)) cell.classList.add('lp-connect-up');
			const upNeighbor = lp.row > 0 ? cells[(lp.row - 1) * 15 + lp.col] : null;
			if (upNeighbor && !lastPlaySet.has(`${lp.row - 1},${lp.col}`)) upNeighbor.classList.add('lp-neighbor-down');
			const leftNeighbor = lp.col > 0 ? cells[lp.row * 15 + lp.col - 1] : null;
			if (leftNeighbor && !lastPlaySet.has(`${lp.row},${lp.col - 1}`)) leftNeighbor.classList.add('lp-neighbor-right');
		}
	}

	for (let r = 0; r < 15; r++) {
		for (let c = 0; c < 15; c++) {
			const cell = cells[r * 15 + c];
			if (!cell.classList.contains('has-tile')) continue;
			if (c < 14) {
				const right = cells[r * 15 + c + 1];
				if (right && right.classList.contains('has-tile')) cell.classList.add('connect-right');
			}
			if (c > 0) {
				const left = cells[r * 15 + c - 1];
				if (left && left.classList.contains('has-tile')) cell.classList.add('connect-left');
			}
			if (r < 14) {
				const down = cells[(r + 1) * 15 + c];
				if (down && down.classList.contains('has-tile')) cell.classList.add('connect-down');
			}
			if (r > 0) {
				const up = cells[(r - 1) * 15 + c];
				if (up && up.classList.contains('has-tile')) cell.classList.add('connect-up');
			}
		}
	}

	for (let r = 0; r < 15; r++) {
		for (let c = 0; c < 15; c++) {
			const key = `${r},${c}`;
			cells[r * 15 + c].setAttribute('aria-label', cellLabel(r, c, cells[r * 15 + c], pendingMap[key], oppPendingMap[key]));
		}
	}

	if (state.preview && state.preview.words && state.preview.words.length > 0) {
		const allValid = state.preview.words.every(w => w.valid);
		const drawOutline = state.preview.status === 'ok' && allValid;
		const cellSet = new Set();
		let sumCol = 0;
		let count = 0;
		for (const word of state.preview.words) {
			if (!word.cells) continue;
			for (const c of word.cells) {
				const key = `${c.row},${c.col}`;
				if (!cellSet.has(key)) {
					cellSet.add(key);
					sumCol += c.col;
					count++;
				}
			}
		}
		let minR = 15, maxR = -1, minC = 15, maxC = -1;
		for (const key of cellSet) {
			const [r, c] = key.split(',').map(Number);
			if (r < minR) minR = r;
			if (r > maxR) maxR = r;
			if (c < minC) minC = c;
			if (c > maxC) maxC = c;
		}
		const isOccupied = (r, c) => {
			if (r < 0 || c < 0 || r >= 15 || c >= 15) return false;
			if (state.board[r] && state.board[r][c]) return true;
			if (state.pending.some(p => p.row === r && p.col === c)) return true;
			return false;
		};
		const anchorCandidates = state.pending.length > 0
			? state.pending.map(p => ({ row: p.row, col: p.col }))
			: Array.from(cellSet).map(k => { const [r, c] = k.split(',').map(Number); return { row: r, col: c }; });
		let bestCorner = pickBubbleCorner(anchorCandidates, isOccupied);
		if (!bestCorner) {
			const firstKey = cellSet.values().next().value;
			const [fr, fc] = firstKey.split(',').map(Number);
			bestCorner = { row: fr, col: fc, isBottomSide: maxR >= 13, isRightSide: (count > 0 && (sumCol / count) >= 7.5) };
		}
		const isRightSide = bestCorner.isRightSide;
		const isBottomSide = bestCorner.isBottomSide;
		const anchor = { row: bestCorner.row, col: bestCorner.col };

		if (drawOutline) {
			const T = 4;
			const inSet = (rr, cc) => cellSet.has(`${rr},${cc}`);
			const addEdge = (left, top, w, h) => {
				const e = document.createElement('div');
				e.className = 'word-outline-edge';
				e.style.left = left + 'px';
				e.style.top = top + 'px';
				e.style.width = w + 'px';
				e.style.height = h + 'px';
				boardEl.appendChild(e);
			};
			for (const key of cellSet) {
				const [r, c] = key.split(',').map(Number);
				const cell = cells[r * 15 + c];
				if (!cell) continue;
				const left = cell.offsetLeft;
				const top = cell.offsetTop;
				const w = cell.offsetWidth;
				const h = cell.offsetHeight;
				if (!inSet(r - 1, c)) {
					const eL = inSet(r - 1, c - 1) ? 0 : T;
					const eR = inSet(r - 1, c + 1) ? 0 : T;
					addEdge(left - eL, top - T, w + eL + eR, T);
				}
				if (!inSet(r + 1, c)) {
					const eL = inSet(r + 1, c - 1) ? 0 : T;
					const eR = inSet(r + 1, c + 1) ? 0 : T;
					addEdge(left - eL, top + h, w + eL + eR, T);
				}
				if (!inSet(r, c - 1)) {
					const eT = inSet(r - 1, c - 1) ? 0 : T;
					const eB = inSet(r + 1, c - 1) ? 0 : T;
					addEdge(left - T, top - eT, T, h + eT + eB);
				}
				if (!inSet(r, c + 1)) {
					const eT = inSet(r - 1, c + 1) ? 0 : T;
					const eB = inSet(r + 1, c + 1) ? 0 : T;
					addEdge(left + w, top - eT, T, h + eT + eB);
				}
			}
		}
		if (anchor && state.preview.status !== 'disconnected') {
			const cell = cells[anchor.row * 15 + anchor.col];
			if (cell) {
				cell.classList.add('has-bubble');
				const bubble = document.createElement('span');
				let bubbleCls = 'score-bubble';
				if (isRightSide) bubbleCls += ' bubble-left';
				if (isBottomSide) bubbleCls += ' bubble-top';
				if (state.challengeMode) bubbleCls += ' bubble-challenge';
				else if (!allValid) bubbleCls += ' bubble-invalid';
				bubble.className = bubbleCls;
				bubble.textContent = state.preview.score;
				cell.appendChild(bubble);
			}
		}
	}

	const showLastBubble = (
		state.lastPlacements && state.lastPlacements.length > 0 &&
		state.lastScore > 0 &&
		state.lastPlayer != null && state.lastPlayer !== state.mySlot &&
		state.current === state.mySlot &&
		state.pending.length === 0
	);
	if (showLastBubble) {
		const isOccupied = (r, c) => {
			if (r < 0 || c < 0 || r >= 15 || c >= 15) return false;
			if (state.board[r] && state.board[r][c]) return true;
			return false;
		};
		let bestCorner = pickBubbleCorner(state.lastPlacements.map(p => ({ row: p.row, col: p.col })), isOccupied);
		if (!bestCorner) {
			const first = state.lastPlacements[0];
			bestCorner = { row: first.row, col: first.col, isBottomSide: false, isRightSide: false };
		}
		const cell = cells[bestCorner.row * 15 + bestCorner.col];
		if (cell) {
			cell.classList.add('has-bubble');
			const bubble = document.createElement('span');
			let bubbleCls = 'score-bubble bubble-challenge bubble-last';
			if (bestCorner.isRightSide) bubbleCls += ' bubble-left';
			if (bestCorner.isBottomSide) bubbleCls += ' bubble-top';
			bubble.className = bubbleCls;
			bubble.textContent = state.lastScore;
			cell.appendChild(bubble);
		}
	}

	const recallBtn = document.getElementById('btn-recall');
	if (recallBtn) {
		const noPending = state.pending.length === 0;
		const dim = noPending && !state.swapMode;
		recallBtn.classList.toggle('disabled-soft', dim);
		recallBtn.title = state.swapMode ? 'Cancel swap' : (noPending ? 'No pending tiles to recall' : 'Recall tiles');
	}
	const submitBtn = document.getElementById('btn-submit');
	if (submitBtn) {
		if (state.swapMode) {
			const hasSelection = state.swapSelection.size > 0;
			submitBtn.classList.toggle('disabled-soft', !hasSelection);
			submitBtn.title = hasSelection ? 'Confirm swap' : 'Tap tiles to swap';
		} else {
			const hasPending = state.pending.length > 0;
			const validatedOk = !!(state.preview && state.preview.status === 'ok');
			const validatedBad = !!(state.preview && state.preview.status && state.preview.status !== 'ok');
			const shouldDim = !hasPending || validatedBad;
			submitBtn.classList.toggle('disabled-soft', shouldDim);
			submitBtn.title = validatedOk
				? 'Submit word'
				: (!hasPending ? 'Place tiles first' : (validatedBad ? 'Not a valid word' : 'Checking…'));
		}
	}
}

function renderTileInto(cell, letter, isBlank) {
	const letterEl = document.createElement('span');
	letterEl.className = 'tile-letter' + (isBlank ? ' tile-blank' : '');
	letterEl.textContent = letter;
	cell.appendChild(letterEl);
	const val = document.createElement('span');
	val.className = 'tile-value';
	val.textContent = isBlank ? '0' : (LETTER_VALUES[letter] || 0);
	cell.appendChild(val);
}

function renderRack() {
	rackEl.innerHTML = '';
	const usedIds = new Set(state.pending.map(p => p.rackId));
	state.rack.forEach((item) => {
		if (usedIds.has(item.id)) {
			const placeholder = document.createElement('div');
			placeholder.className = 'rack-tile rack-placeholder';
			placeholder.setAttribute('aria-hidden', 'true');
			rackEl.appendChild(placeholder);
			return;
		}
		const tile = document.createElement('div');
		tile.className = 'rack-tile';
		if (item.letter === '_') tile.classList.add('blank-tile');
		if (state.swapMode && state.swapSelection.has(item.id)) tile.classList.add('swap-mark');
		if (drag && drag.source === 'rack' && drag.rackId === item.id) tile.classList.add('dragging');
		const display = item.letter === '_' ? '?' : item.letter;
		tile.innerHTML = `${display}<span class="tile-value">${item.letter === '_' ? '0' : (LETTER_VALUES[item.letter] || 0)}</span>`;
		tile.dataset.id = item.id;
		tile.setAttribute('role', 'img');
		tile.setAttribute('aria-label', (item.letter === '_' ? 'blank' : `${item.letter}, ${LETTER_VALUES[item.letter] || 0} point${LETTER_VALUES[item.letter] === 1 ? '' : 's'}`) + (tile.classList.contains('swap-mark') ? ', selected for swap' : ''));
		tile.addEventListener('pointerdown', e => onRackPointerDown(e, item.id));
		rackEl.appendChild(tile);
	});
	syncAppPadding();
}

function renderScores() {
	const nAll = Math.max(2, Math.min(4, state.maxPlayers || (state.scores ? state.scores.length : 2)));
	const forfeited = new Set(state.forfeitedSlots || []);
	const activeSlots = [];
	for (let i = 0; i < nAll; i++) if (!forfeited.has(i)) activeSlots.push(i);
	const bar = document.getElementById('score-bar');
	if (!bar) return;
	const total = activeSlots.reduce((a, i) => a + (state.scores[i] || 0), 0);
	let html = '';
	activeSlots.forEach((i, idx) => {
		const sc = state.scores[i] || 0;
		const pct = total === 0 ? (100 / activeSlots.length) : (sc / total) * 100;
		const isActive = state.current === i;
		const name = state.names[i] || `P${i + 1}`;
		const labelHtml = `<span class="player-label" id="name-label-${i}">${avatarHtml(state.userIds[i])}${escapeHtml(name)}</span>`;
		const scoreHtml = `<span class="player-score" id="score-val-${i}">${sc}</span>`;
		const innerOrder = (idx === activeSlots.length - 1) ? `${scoreHtml}${labelHtml}` : `${labelHtml}${scoreHtml}`;
		html += `<div class="score-half p${i}${isActive ? ' active' : ''}" id="score-tag-p${i}" style="flex-basis:${pct}%">${innerOrder}</div>`;
	});
	bar.innerHTML = html;
}

function getPoint(e) {
	return { x: e.clientX, y: e.clientY };
}

function createGhost(letter, isBlank) {
	const g = document.createElement('div');
	g.className = 'drag-ghost' + (isBlank ? ' blank-tile' : '');
	g.setAttribute('aria-hidden', 'true');
	const display = letter === '_' ? '?' : letter;
	const value = letter === '_' || isBlank ? '0' : (LETTER_VALUES[letter] || 0);
	g.innerHTML = `${display}<span class="tile-value">${value}</span>`;
	document.body.appendChild(g);
	return g;
}

function moveGhost(p) {
	if (drag && drag.ghost) {
		drag.ghost.style.left = p.x + 'px';
		drag.ghost.style.top = p.y + 'px';
	}
}

function clearDropTargets() {
	boardEl.querySelectorAll('.cell.drop-target').forEach(c => c.classList.remove('drop-target'));
}

function isCellValidTarget(cell) {
	if (!cell) return false;
	const r = +cell.dataset.r;
	const c = +cell.dataset.c;
	if (!Number.isFinite(r) || !Number.isFinite(c)) return false;
	if (state.board[r] && state.board[r][c]) return false;
	if (state.bonus[r] && state.bonus[r][c] === 'XX') return false;
	const conflict = state.pending.find(pp => pp.row === r && pp.col === c);
	if (conflict && !(drag && drag.source === 'board' && drag.pendingIdx === state.pending.indexOf(conflict))) return false;
	return true;
}

function findValidCellNear(p) {
	const direct = document.elementFromPoint(p.x, p.y);
	if (direct && direct.closest('#rack, .rack-wrap, .rack-tile')) return null;
	const directCell = direct ? direct.closest('.cell') : null;
	if (directCell && isCellValidTarget(directCell)) return directCell;

	const boardRect = boardEl.getBoundingClientRect();
	const N = (state.bonus && state.bonus.length) || 15;
	const cellW = boardRect.width / N;
	const cellH = boardRect.height / N;
	if (cellW <= 0 || cellH <= 0) return null;

	const cx = (p.x - boardRect.left) / cellW;
	const cy = (p.y - boardRect.top) / cellH;
	if (cx < -0.5 || cy < -0.5 || cx > N + 0.5 || cy > N + 0.5) return null;

	const baseR = Math.floor(cy);
	const baseC = Math.floor(cx);
	const maxDist = Math.max(cellW, cellH) * 0.75;

	let best = null;
	let bestDist = Infinity;
	for (let dr = -1; dr <= 1; dr++) {
		for (let dc = -1; dc <= 1; dc++) {
			const r = baseR + dr;
			const c = baseC + dc;
			if (r < 0 || c < 0 || r >= N || c >= N) continue;
			const cell = boardEl.querySelector(`.cell[data-r="${r}"][data-c="${c}"]`);
			if (!isCellValidTarget(cell)) continue;
			const rect = cell.getBoundingClientRect();
			const ccx = rect.left + rect.width / 2;
			const ccy = rect.top + rect.height / 2;
			const dist = Math.hypot(ccx - p.x, ccy - p.y);
			if (dist < bestDist && dist <= maxDist) {
				bestDist = dist;
				best = cell;
			}
		}
	}
	return best;
}

function highlightCellUnder(p) {
	clearDropTargets();
	const cell = findValidCellNear(p);
	if (!cell) return null;
	cell.classList.add('drop-target');
	return { type: 'cell', r: +cell.dataset.r, c: +cell.dataset.c };
}

function flipReorderRack(fromId, toId) {
	const firsts = {};
	const tiles = Array.from(rackEl.children);
	tiles.forEach(t => {
		firsts[+t.dataset.id] = t.getBoundingClientRect();
	});

	const fromIdx = state.rack.findIndex(r => r.id === fromId);
	const origToIdx = state.rack.findIndex(r => r.id === toId);
	if (fromIdx < 0 || origToIdx < 0 || fromIdx === origToIdx) return;
	const [item] = state.rack.splice(fromIdx, 1);
	let toIdx = state.rack.findIndex(r => r.id === toId);
	if (fromIdx < origToIdx) toIdx += 1;
	state.rack.splice(toIdx, 0, item);

	state.rack.forEach(rackItem => {
		const t = rackEl.querySelector(`[data-id="${rackItem.id}"]`);
		if (t) rackEl.appendChild(t);
	});

	tiles.forEach(t => {
		const id = +t.dataset.id;
		if (!firsts[id]) return;
		const last = t.getBoundingClientRect();
		const dx = firsts[id].left - last.left;
		const dy = firsts[id].top - last.top;
		if (!dx && !dy) return;
		t.style.transition = 'none';
		t.style.transform = `translate(${dx}px, ${dy}px)`;
		requestAnimationFrame(() => {
			t.style.transition = 'transform 0.2s cubic-bezier(0.25, 0.8, 0.25, 1)';
			t.style.transform = '';
		});
	});
}

function onRackPointerDown(e, rackId) {
	if (state.swapMode) {
		if (state.swapSelection.has(rackId)) state.swapSelection.delete(rackId);
		else state.swapSelection.add(rackId);
		renderRack();
		renderBoard();
		return;
	}
	if (state.pending.some(p => p.rackId === rackId)) return;
	const item = state.rack.find(r => r.id === rackId);
	if (!item) return;
	e.preventDefault();
	drag = {
		source: 'rack',
		rackId,
		letter: item.letter,
		isBlank: item.letter === '_',
		pointerId: e.pointerId,
		ghost: createGhost(item.letter, item.letter === '_'),
	};
	moveGhost(getPoint(e));
	renderRack();
	e.target.setPointerCapture && e.target.setPointerCapture(e.pointerId);
}

let starTapCount = 0;
let starTapLastAt = 0;
let starTapLastCell = null;

function handleStarTap(r, c) {
	if (state.bonus[r] && state.bonus[r][c] !== 'ST') return false;
	if (state.board[r][c]) return false;
	if (state.pending.some(p => p.row === r && p.col === c)) return false;
	const now = Date.now();
	const cellKey = `${r},${c}`;
	if (now - starTapLastAt > 1200 || starTapLastCell !== cellKey) {
		starTapCount = 0;
	}
	starTapCount += 1;
	starTapLastAt = now;
	starTapLastCell = cellKey;
	if (starTapCount >= 3) {
		starTapCount = 0;
		setPartyMode(!partyMode);
		return true;
	}
	return false;
}

let sharePendingTimer = null;
function sharePendingSoon() {
	if (!state.transparentMode || state.isSpectator) return;
	if (sharePendingTimer) clearTimeout(sharePendingTimer);
	sharePendingTimer = setTimeout(() => {
		sharePendingTimer = null;
		const placements = state.pending.map(p => ({
			row: p.row, col: p.col, letter: p.letter, blank: !!p.blank,
		}));
		fetch('/share_pending', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ ...authParams(), placements }),
		}).catch(() => {});
	}, 80);
}

function getViewportSize() {
	const wrap = boardEl.parentElement;
	if (!wrap) return boardEl.offsetWidth || 0;
	return Math.min(wrap.clientWidth, wrap.clientHeight);
}

function applyZoom() {
	if (zoomState.active) {
		boardEl.style.transform = `translate(${zoomState.tx}px, ${zoomState.ty}px) scale(${ZOOM_SCALE})`;
	} else {
		boardEl.style.transform = '';
	}
}

function clampPan() {
	const V = getViewportSize();
	const B = boardEl.offsetWidth || V;
	const min = V - B * ZOOM_SCALE;
	if (zoomState.tx > 0) zoomState.tx = 0;
	if (zoomState.tx < min) zoomState.tx = min;
	if (zoomState.ty > 0) zoomState.ty = 0;
	if (zoomState.ty < min) zoomState.ty = min;
}

function panToCell(r, c) {
	if (!zoomState.active) return;
	const V = getViewportSize();
	const B = boardEl.offsetWidth || V;
	const cellSize = B / 15;
	const cx = cellSize * (c + 0.5);
	const cy = cellSize * (r + 0.5);
	zoomState.tx = V / 2 - cx * ZOOM_SCALE;
	zoomState.ty = V / 2 - cy * ZOOM_SCALE;
	clampPan();
	applyZoom();
}

function zoomIn(focusR, focusC) {
	zoomState.active = true;
	if (focusR != null && focusC != null) {
		panToCell(focusR, focusC);
	} else {
		panToCell(7, 7);
	}
}

function zoomOut() {
	zoomState.active = false;
	zoomState.tx = 0;
	zoomState.ty = 0;
	applyZoom();
}

function toggleZoom() {
	if (zoomState.active) zoomOut();
	else zoomIn();
}

function onCellPointerDown(e) {
	if (state.swapMode) return;
	if (pinchActive) return;
	const cell = e.currentTarget;
	const r = +cell.dataset.r;
	const c = +cell.dataset.c;
	if (state.bonus[r] && state.bonus[r][c] === 'ST' && !state.board[r][c] && !state.pending.some(p => p.row === r && p.col === c)) {
		e.preventDefault();
		if (handleStarTap(r, c)) return;
		return;
	}
	const pendingIdx = state.pending.findIndex(p => p.row === r && p.col === c);
	if (pendingIdx === -1) {
		e.preventDefault();
		panSession = {
			pointerId: e.pointerId,
			startX: e.clientX,
			startY: e.clientY,
			startTx: zoomState.tx,
			startTy: zoomState.ty,
			moved: false,
		};
		try { cell.setPointerCapture && cell.setPointerCapture(e.pointerId); } catch (err) {}
		return;
	}
	e.preventDefault();
	const p = state.pending[pendingIdx];
	drag = {
		source: 'board',
		pendingIdx,
		letter: p.letter,
		isBlank: p.blank,
		rackId: p.rackId,
		pointerId: e.pointerId,
		ghost: createGhost(p.letter, p.blank),
	};
	moveGhost(getPoint(e));
	cell.setPointerCapture && cell.setPointerCapture(e.pointerId);
}

function onPointerMove(e) {
	if (panSession && e.pointerId === panSession.pointerId) {
		const dx = e.clientX - panSession.startX;
		const dy = e.clientY - panSession.startY;
		if (!panSession.moved && (Math.abs(dx) > 5 || Math.abs(dy) > 5)) {
			panSession.moved = true;
		}
		if (panSession.moved && zoomState.active) {
			e.preventDefault();
			boardEl.style.transition = 'none';
			zoomState.tx = panSession.startTx + dx;
			zoomState.ty = panSession.startTy + dy;
			clampPan();
			applyZoom();
		}
		return;
	}
	if (!drag) return;
	e.preventDefault();
	const p = getPoint(e);
	moveGhost(p);

	if (drag.source === 'rack') {
		const el = document.elementFromPoint(p.x, p.y);
		const rackTile = el ? el.closest('.rack-tile') : null;
		if (rackTile) {
			const targetId = +rackTile.dataset.id;
			if (targetId === drag.rackId) {
				drag.lastOverId = targetId;
				clearDropTargets();
				return;
			}
			if (targetId !== drag.lastOverId) {
				drag.lastOverId = targetId;
				flipReorderRack(drag.rackId, targetId);
			}
			clearDropTargets();
			return;
		}
		drag.lastOverId = null;
	}

	highlightCellUnder(p);
}

function onPointerUp(e) {
	if (panSession && e.pointerId === panSession.pointerId) {
		const wasTap = !panSession.moved;
		boardEl.style.transition = '';
		panSession = null;
		if (wasTap) {
			const now = Date.now();
			if (now - lastZoomTapAt < 350) {
				toggleZoom();
				lastZoomTapAt = 0;
			} else {
				lastZoomTapAt = now;
			}
		}
		return;
	}
	if (!drag) return;
	e.preventDefault();
	const p = getPoint(e);
	clearDropTargets();
	if (drag.ghost) drag.ghost.remove();

	const cellEl = findValidCellNear(p);

	let placed = false;
	if (cellEl) {
		const r = +cellEl.dataset.r;
		const c = +cellEl.dataset.c;
		if (drag.source === 'rack') {
			if (drag.isBlank) {
				askBlankLetter(drag.rackId, r, c);
			} else {
				state.pending.push({
					row: r, col: c, letter: drag.letter,
					blank: false, rackId: drag.rackId,
				});
				soundTilePlace();
			}
		} else {
			const pp = state.pending[drag.pendingIdx];
			if (pp.blank) {
				askBlankLetter(pp.rackId, r, c, drag.pendingIdx);
			} else {
				pp.row = r;
				pp.col = c;
			}
		}
		placed = true;
		clearMessage();
		const placedR = +cellEl.dataset.r;
		const placedC = +cellEl.dataset.c;
		if (zoomState.active) panToCell(placedR, placedC);
		else zoomIn(placedR, placedC);
	}

	if (!placed && drag.source === 'board') {
		const dropEl = document.elementFromPoint(p.x, p.y);
		const rackTile = dropEl ? dropEl.closest('.rack-tile') : null;
		const rackArea = dropEl ? dropEl.closest('#rack, .rack-wrap') : null;
		const fromIdx = state.rack.findIndex(r => r.id === drag.rackId);
		if (fromIdx >= 0 && (rackTile || rackArea)) {
			const [item] = state.rack.splice(fromIdx, 1);
			if (rackTile && +rackTile.dataset.id !== drag.rackId) {
				const targetId = +rackTile.dataset.id;
				let toIdx = state.rack.findIndex(r => r.id === targetId);
				if (toIdx < 0) toIdx = state.rack.length;
				const targetRect = rackTile.getBoundingClientRect();
				if (p.x > targetRect.left + targetRect.width / 2) toIdx += 1;
				state.rack.splice(toIdx, 0, item);
			} else {
				state.rack.splice(fromIdx, 0, item);
			}
		}
		state.pending.splice(drag.pendingIdx, 1);
	}

	drag = null;
	renderBoard();
	renderRack();
	refreshPreview();
	sharePendingSoon();
}

function onPointerCancel(e) {
	if (panSession && e && e.pointerId === panSession.pointerId) {
		panSession = null;
		return;
	}
	if (!drag) return;
	if (drag.ghost) drag.ghost.remove();
	clearDropTargets();
	drag = null;
	renderRack();
}

function onPinchPointerDown(e) {
	if (e.pointerType !== 'touch') return;
	if (!boardEl.contains(e.target)) return;
	pinchPointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
	if (pinchPointers.size === 2) {
		const pts = [...pinchPointers.values()];
		pinchInitialDist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
		pinchActive = true;
		if (drag) {
			if (drag.ghost) drag.ghost.remove();
			drag = null;
			clearDropTargets();
			renderRack();
		}
		panSession = null;
	}
}

function onPinchPointerMove(e) {
	if (e.pointerType !== 'touch') return;
	if (!pinchPointers.has(e.pointerId)) return;
	pinchPointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
	if (!pinchActive || pinchPointers.size !== 2) return;
	e.preventDefault();
	const pts = [...pinchPointers.values()];
	const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
	const ratio = dist / pinchInitialDist;
	if (ratio > 1.25 && !zoomState.active) {
		zoomIn();
		pinchInitialDist = dist;
	} else if (ratio < 0.8 && zoomState.active) {
		zoomOut();
		pinchInitialDist = dist;
	}
}

function onPinchPointerEnd(e) {
	pinchPointers.delete(e.pointerId);
	if (pinchPointers.size < 2) pinchActive = false;
}

window.addEventListener('resize', () => {
	if (zoomState.active) { clampPan(); applyZoom(); }
});

document.addEventListener('pointerdown', onPinchPointerDown, { passive: false, capture: true });
document.addEventListener('pointermove', onPinchPointerMove, { passive: false, capture: true });
document.addEventListener('pointerup', onPinchPointerEnd, { passive: true, capture: true });
document.addEventListener('pointercancel', onPinchPointerEnd, { passive: true, capture: true });

document.addEventListener('pointermove', onPointerMove, { passive: false });
document.addEventListener('pointerup', onPointerUp, { passive: false });
document.addEventListener('pointercancel', onPointerCancel);

function askBlankLetter(rackId, r, c, pendingIdx) {
	blankLetters.innerHTML = '';
	const openedAt = Date.now();
	for (let i = 0; i < 26; i++) {
		const L = String.fromCharCode(65 + i);
		const b = document.createElement('button');
		b.className = 'blank-letter-btn';
		b.textContent = L;
		b.addEventListener('click', (ev) => {
			if (Date.now() - openedAt < 400) {
				ev.preventDefault();
				ev.stopPropagation();
				return;
			}
			soundTilePlace();
			if (typeof pendingIdx === 'number' && state.pending[pendingIdx]) {
				const pp = state.pending[pendingIdx];
				pp.row = r;
				pp.col = c;
				pp.letter = L;
			} else {
				state.pending.push({ row: r, col: c, letter: L, blank: true, rackId });
			}
			blankOverlay.classList.add('hidden');
			renderBoard();
			renderRack();
			refreshPreview();
			sharePendingSoon();
		});
		blankLetters.appendChild(b);
	}
	blankOverlay.classList.remove('hidden');
}

document.getElementById('btn-blank-cancel').addEventListener('click', () => {
	blankOverlay.classList.add('hidden');
});

function showMessage(text, kind) {
	messageBar.textContent = text;
	messageBar.className = kind || '';
	announce(text);
}

function clearMessage() {
	messageBar.textContent = '';
	messageBar.className = '';
}

async function loadState(preservePending) {
	if (!state.roomId || !state.playerToken) return false;
	const r = await fetch('/state?room_id=' + encodeURIComponent(state.roomId), { headers: { 'X-Player-Token': state.playerToken } });
	if (r.status === 404) {
		clearCreds();
		showLanding();
		return false;
	}
	const s = await r.json();
	const prevCurrent = state.current;
	const prevNames = state.names;
	const prevRoom = state.namesRoom;
	const prevSig = `${state.lastPlayer}|${state.lastAction}|${(state.lastWords||[]).join(',')}`;
	state.board = s.board;
	state.blanks = s.blanks;
	state.bonus = s.bonus;
	state.rack = mergeRack(state.rack, s.rack);
	state.scores = s.scores;
	// Clear hint highlight when the turn moves away from me
	if (state.current !== s.current && state.hintCells) {
		state.hintCells = null;
	}
	state.current = s.current;
	state.mySlot = s.my_slot;
	state.isSpectator = !!s.is_spectator;
	document.body.classList.toggle('is-spectator', state.isSpectator);
	state.hasOpponent = !!s.has_opponent;
	state.isFull = !!s.is_full;
	state.names = s.names || ['P1', 'P2'];
	state.namesRoom = state.roomId;
	state.lastAction = s.last_action || null;
	state.gameOver = !!s.game_over;
	state.gameResult = s.game_result || null;
	state.consecutivePasses = s.consecutive_passes || 0;
	state.mode = s.mode || 'classic';
	state.speedMode = !!s.speed_mode;
	state.challengeMode = !!s.challenge_mode;
	state.vsComputer = !!s.vs_computer;
	state.aiDifficulty = s.ai_difficulty || 50;
	state.aiStyle = s.ai_style || 'score';
	state.aiThinking = !!s.ai_thinking;
	state.aiPlayAfter = s.ai_play_after || 0;
	state.noMoves = !!s.no_moves;
	state.forfeitedSlots = s.forfeited_slots || [];
	state.rematchDeclinedBy = (s.rematch_declined_by != null) ? s.rematch_declined_by : null;
	state.history = s.history || [];
	state.hintTokens = s.hint_tokens || 0;
	state.canChallenge = !!s.can_challenge;
	state.transparentMode = !!s.transparent_mode;
	state.pendingViews = s.pending_views || {};
	state.turnDeadline = s.turn_deadline || null;
	state.maxTurns = s.max_turns || 5;
	state.missedTurns = s.missed_turns || [0, 0];
	state.rematch = s.rematch || [false, false];
	state.unseenLetters = s.unseen_letters || {};
	state.tileOwners = s.tile_owners || [];
	state.colors = s.colors || ['#ffe49f', '#ffe49f', '#ffe49f', '#ffe49f'];
	state.maxPlayers = s.max_players || 2;
	state.moves = s.moves || [0, 0];
	state.chat = s.chat || [];
	state.userIds = s.user_ids || [];
	state.chatSeq = s.chat_seq || 0;
	state.finalSummary = s.final_summary || null;
	if (!state.finalSummary) state.endgameShown = false;
	const serverParty = s.party_played_at || 0;
	const partyKey = `scrabble_party_played_${state.roomId}`;
	const storedParty = parseInt(localStorage.getItem(partyKey) || '0', 10);
	if (serverParty && serverParty > storedParty) {
		localStorage.setItem(partyKey, String(serverParty));
		if (!partyMode) setPartyMode(true);
	}
	for (let i = 0; i < 4; i++) {
		document.documentElement.style.setProperty(`--tile-color-${i}`, state.colors[i] || '#ffe49f');
	}
	if (state.mySlot !== null) {
		document.documentElement.style.setProperty('--my-tile-color', state.colors[state.mySlot]);
	}
	state.bagCount = s.bag_count;
	state.firstMove = s.first_move;
	state.lastPlayer = s.last_player;
	state.lastWords = s.last_words || [];
	state.lastScore = s.last_score || 0;
	state.lastDefinitions = s.last_definitions || [];
	state.lastPlacements = s.last_placements || [];
	state.lastWordCells = s.last_word_cells || [];
	if (!preservePending) {
		state.pending = [];
		state.preview = null;
		state.swapMode = false;
		state.swapSelection.clear();
	}
	renderBoard();
	renderRack();
	renderScores();
	renderInfoPanel();
	renderTurnState();
	syncAppPadding();

	const newSig = `${state.lastPlayer}|${state.lastAction}|${(state.lastWords||[]).join(',')}`;
	const opponentActed = newSig !== prevSig && state.lastPlayer !== null && state.lastPlayer !== state.mySlot;
	const joined = prevRoom === state.roomId ? state.names.filter((n, i) => i !== state.mySlot && n && !prevNames[i]) : [];
	const spoken = [];
	if (joined.length) spoken.push(`${joined.join(', ')} joined`);
	if (newSig !== prevSig) spoken.push(describeLastAction());
	if (!state.gameOver && state.isFull && (newSig !== prevSig || state.current !== prevCurrent)) {
		spoken.push(state.current === state.mySlot ? 'Your turn' : `${state.names[state.current] || `P${state.current + 1}`}'s turn`);
	}
	if (joined.length) {
		fireNotification('Scrabble', `${joined.join(', ')} joined the room`);
	} else if (opponentActed) {
		const desc = describeLastAction();
		const yourTurn = state.current === state.mySlot ? ' — Your turn!' : '';
		if (desc) fireNotification('Scrabble', desc + yourTurn);
		soundOpponentPlay();
		if (state.pending.length > 0) {
			state.pending = [];
			state.preview = null;
			state.swapMode = false;
			state.swapSelection.clear();
			renderBoard();
			renderRack();
		}
		if (state.current === state.mySlot) zoomOut();
	} else if (prevCurrent !== state.mySlot && state.current === state.mySlot) {
		fireNotification('Scrabble', 'Your turn!');
		zoomOut();
	}

	const newestChat = state.chat.length ? state.chat[state.chat.length - 1] : null;
	if (newestChat && newestChat.id > state.lastNotifiedChatId && newestChat.slot !== state.mySlot) {
		soundChatPing();
		fireNotification(`${newestChat.name}`, newestChat.text);
		spoken.push(`${newestChat.name} says: ${newestChat.text}`);
		state.lastNotifiedChatId = newestChat.id;
	}
	if (newestChat && newestChat.id > state.lastSeenChatId && newestChat.slot !== state.mySlot) {
		if (!state.chatOpen) {
			state.unreadChat = state.chat.filter(m => m.id > state.lastSeenChatId && m.slot !== state.mySlot).length;
			renderChatBadge();
		} else {
			state.lastSeenChatId = newestChat.id;
			const key = chatSeenKey();
			if (key) localStorage.setItem(key, String(state.lastSeenChatId));
		}
	}
	if (state.chatOpen) renderChat();

	if (state.finalSummary && !state.endgameShown && state.gameOver) {
		state.endgameShown = true;
		showEndGame();
		soundEndGame();
		const winnerEl = endgameBody.querySelector('.endgame-winner');
		spoken.push(endgameTitle.textContent, winnerEl ? winnerEl.textContent : '');
	}
	if (!state.gameOver && endgameOverlay && !endgameOverlay.classList.contains('hidden')) {
		endgameOverlay.classList.add('hidden');
		state.endgameShown = false;
	}
	if (state.gameOver && endgameOverlay && !endgameOverlay.classList.contains('hidden')) {
		updateRematchStatus();
	}
	announce(spoken.filter(Boolean).join('. '));
	return true;
}

function updateRematchStatus() {
	const status = document.getElementById('rematch-status');
	const btn = document.getElementById('btn-rematch');
	const declineBtn = document.getElementById('btn-rematch-decline');
	if (!status || !btn) return;
	const closed = (state.forfeitedSlots || []).length > 0; // the forfeiter can never accept, so a rematch can't happen
	btn.style.display = closed ? 'none' : ''; // .btn sets display: flex, so the hidden class has no effect here
	if (closed) {
		status.textContent = '';
		if (declineBtn) declineBtn.classList.add('hidden');
		return;
	}
	const rematch = state.rematch || [];
	const me = !!rematch[state.mySlot];
	const forfeited = new Set(state.forfeitedSlots || []);
	const activeOthers = [];
	for (let i = 0; i < rematch.length; i++) {
		if (i === state.mySlot || forfeited.has(i)) continue;
		activeOthers.push(i);
	}
	const someoneElseWants = activeOthers.some(i => rematch[i]);
	const allActiveOthers = activeOthers.length > 0 && activeOthers.every(i => rematch[i]);
	if (state.rematchDeclinedBy != null && state.rematchDeclinedBy !== state.mySlot) {
		const name = state.names[state.rematchDeclinedBy] || 'Opponent';
		status.textContent = `${name} declined the rematch`;
		btn.disabled = false;
		btn.textContent = 'Play again';
		if (declineBtn) declineBtn.classList.add('hidden');
		return;
	}
	if (me && allActiveOthers) {
		status.textContent = 'Starting…';
		btn.disabled = true;
		if (declineBtn) declineBtn.classList.add('hidden');
	} else if (me) {
		status.textContent = 'Waiting for opponent…';
		btn.disabled = true;
		btn.textContent = 'Waiting…';
		if (declineBtn) declineBtn.classList.add('hidden');
	} else if (someoneElseWants) {
		status.textContent = 'Opponent wants a rematch';
		btn.disabled = false;
		btn.textContent = 'Accept rematch';
		if (declineBtn) declineBtn.classList.remove('hidden');
	} else {
		status.textContent = '';
		btn.disabled = false;
		btn.textContent = 'Play again';
		if (declineBtn) declineBtn.classList.add('hidden');
	}
}

function renderChatBadge() {
	const text = state.unreadChat > 99 ? '99+' : String(state.unreadChat);
	for (const id of ['chat-badge', 'chat-badge-menu']) {
		const badge = document.getElementById(id);
		if (!badge) continue;
		if (state.unreadChat > 0) {
			badge.textContent = text;
			badge.classList.remove('hidden');
		} else {
			badge.classList.add('hidden');
		}
	}
}

function renderNoMovesBanner() {
	const el = document.getElementById('no-moves-banner');
	if (!el) return;
	let show = false;
	let text = '';
	if (state.aiThinking) {
		show = true;
		text = 'Computer is thinking…';
	} else if (state.noMoves && !state.gameOver) {
		show = true;
		text = 'No possible moves with your rack — pass or swap to continue';
	}
	el.textContent = text;
	el.classList.toggle('hidden', !show);
}

function renderModeInfo() {
	const el = document.getElementById('game-mode-info');
	if (!el) return;
	const chips = [];
	const modeName = (state.mode || 'classic').toUpperCase();
	chips.push(`<span class="mode-chip mode-chip-base">${modeName}</span>`);
	if (state.vsComputer) {
		const label = state.aiStyle === 'stacker' ? 'VS STACKER' : `VS CPU · DIFF ${state.aiDifficulty}`;
		chips.push(`<span class="mode-chip mode-chip-ai">${label}</span>`);
	}
	if (state.challengeMode) chips.push(`<span class="mode-chip mode-chip-challenge">CHALLENGE</span>`);
	if (state.speedMode) chips.push(`<span class="mode-chip mode-chip-speed">SPEED</span>`);
	if (state.transparentMode) chips.push(`<span class="mode-chip mode-chip-transparent">TRANSPARENT</span>`);
	if (state.speedMode) {
		const maxT = state.maxTurns || 5;
		const moves = state.moves || [];
		let used = 0;
		for (let i = 0; i < (state.maxPlayers || 2); i++) used = Math.max(used, moves[i] || 0);
		const left = Math.max(0, maxT - used);
		chips.push(`<span class="mode-chip mode-chip-plays">${left} PLAY${left === 1 ? '' : 'S'} LEFT</span>`);
	}
	el.innerHTML = chips.join('');
}

function renderTurnState() {
	renderModeInfo();
	renderNoMovesBanner();
	document.body.classList.toggle('challenge-mode', !!state.challengeMode);
	const hintBtn = document.getElementById('btn-menu-hint');
	if (hintBtn) {
		const t = state.hintTokens || 0;
		hintBtn.classList.toggle('hidden', t < 1);
		const badge = document.getElementById('hint-badge');
		if (badge) {
			if (t > 1) { badge.textContent = t; badge.classList.remove('hidden'); }
			else { badge.classList.add('hidden'); }
		}
	}
	const hintStar = document.getElementById('hint-star');
	if (hintStar) hintStar.classList.toggle('hidden', (state.hintTokens || 0) < 1);
	const turn = document.getElementById('turn-indicator');
	const app = document.getElementById('app');
	const swapBtn = document.getElementById('btn-menu-swap');
	if (!turn || !app) return;
	let label = '', cls = 'turn-indicator waiting', notMyTurn = true;
	if (state.gameOver) {
		label = state.gameResult === 'draw' ? 'Game over — draw' : 'Game over';
	} else if (!state.isFull) {
		const need = (state.maxPlayers || 2) - (state.scores ? state.scores.filter((_, i) => state.names[i]).length : 1);
		label = state.maxPlayers > 2 ? `Waiting for ${need} more player${need === 1 ? '' : 's'}…` : 'Waiting for opponent…';
	} else if (state.current === state.mySlot) {
		label = 'Your turn';
		cls = 'turn-indicator your-turn';
		notMyTurn = false;
	} else {
		label = `${state.names[state.current] || `P${state.current + 1}`}'s turn`;
	}
	if (state.speedMode && !state.gameOver && state.hasOpponent && state.turnDeadline) {
		const remaining = Math.max(0, Math.ceil(state.turnDeadline - Date.now() / 1000));
		label += ` — ${remaining}s`;
		if (remaining <= 10) cls += ' timer-warn';
		const forfeited = new Set(state.forfeitedSlots || []);
		const misses = (state.missedTurns || []).slice(0, state.maxPlayers || 2).filter((_, i) => !forfeited.has(i));
		if (misses.some(m => m)) label += ` (misses ${misses.join('/')})`;
	}
	turn.textContent = label;
	turn.className = cls;
	app.classList.toggle('not-my-turn', notMyTurn);
	if (swapBtn) {
		const swapBlocked = state.bagCount < 7 || notMyTurn;
		swapBtn.classList.toggle('disabled-soft', swapBlocked);
		swapBtn.title = notMyTurn
			? 'Wait for your turn to swap'
			: (state.bagCount < 7 ? 'Cannot swap when bag has fewer than 7 tiles' : 'Swap tiles with bag');
	}
	const passBtn = document.getElementById('btn-menu-pass');
	if (passBtn) {
		passBtn.classList.toggle('disabled-soft', notMyTurn);
		passBtn.title = notMyTurn ? 'Wait for your turn to pass' : 'Pass turn';
	}
	const recallBtn = document.getElementById('btn-recall');
	if (recallBtn) {
		const noPending = state.pending.length === 0;
		const dim = noPending && !state.swapMode;
		recallBtn.classList.toggle('disabled-soft', dim);
		recallBtn.title = state.swapMode ? 'Cancel swap' : (noPending ? 'No pending tiles to recall' : 'Recall tiles');
	}
}

setInterval(() => {
	if (state.speedMode && !state.gameOver) renderTurnState();
}, 1000);

function renderInfoPanel() {
	const histBtn = document.getElementById('btn-history');
	if (histBtn) histBtn.classList.toggle('hidden', !(state.history && state.history.length > 0));

	if (!state.lastAction || state.lastPlayer === null) {
		lastBox.classList.add('hidden');
		return;
	}

	lastBox.classList.remove('hidden');
	const isPlay = state.lastAction === 'play' && state.lastWords.length > 0;
	const isSurvived = state.lastAction === 'challenge_lost' && state.lastWords.length > 0
		&& state.lastDefinitions && state.lastDefinitions.length > 0;
	const hasDefs = (state.lastDefinitions || []).some(d => d && d.entry);
	const showChevron = ((isPlay && !state.challengeMode) || isSurvived) && hasDefs;
	lastBox.classList.toggle('no-chevron', !showChevron);
	lastBox.dataset.clickable = showChevron ? '1' : '0';
	const name = state.names[state.lastPlayer] || `P${state.lastPlayer + 1}`;
	lastSummary.innerHTML = '';

	if (isPlay) {
		const words = state.lastWords.join(', ');
		const firstDef = state.lastDefinitions.find(d => d.entry);
		const defSnippet = firstDef && firstDef.entry ? ` — ${firstDef.entry.definition}` : '';
		const main = document.createTextNode(`${name} PLAYED ${words}`);
		const pts = document.createElement('span');
		pts.className = 'last-pts';
		pts.textContent = `+${state.lastScore}`;
		const def = document.createElement('span');
		def.className = 'last-def';
		def.textContent = defSnippet;
		lastSummary.appendChild(main);
		lastSummary.appendChild(pts);
		lastSummary.appendChild(def);
	} else if (state.lastAction === 'swap') {
		lastSummary.appendChild(document.createTextNode(`${name} SWAPPED TILES`));
	} else if (state.lastAction === 'pass') {
		lastSummary.appendChild(document.createTextNode(`${name} PASSED THEIR TURN`));
	} else if (state.lastAction === 'challenge_won') {
		const bad = (state.lastWords || []).join(', ');
		lastSummary.appendChild(document.createTextNode(`${name} CHALLENGED — INVALID: ${bad}`));
	} else if (state.lastAction === 'challenge_lost') {
		const words = (state.lastWords || []).join(', ');
		const firstDef = (state.lastDefinitions || []).find(d => d.entry);
		const defSnippet = firstDef && firstDef.entry ? ` — ${firstDef.entry.definition}` : '';
		const main = document.createTextNode(`${name} CHALLENGED ${words} — VALID, TURN LOST`);
		const def = document.createElement('span');
		def.className = 'last-def';
		def.textContent = defSnippet;
		lastSummary.appendChild(main);
		lastSummary.appendChild(def);
	} else {
		lastBox.classList.add('hidden');
	}
}

function openDefinitions() {
	if (state.lastWords.length === 0) return;
	if (lastBox.dataset.clickable !== '1') return;
	defList.innerHTML = '';
	for (let i = 0; i < state.lastWords.length; i++) {
		const word = state.lastWords[i];
		const d = state.lastDefinitions[i];
		const entry = document.createElement('div');
		entry.className = 'def-modal-entry';
		const wEl = document.createElement('div');
		wEl.className = 'def-word';
		wEl.textContent = word;
		entry.appendChild(wEl);
		if (d && d.entry) {
			if (d.entry.partOfSpeech) {
				const pos = document.createElement('div');
				pos.className = 'def-pos';
				pos.textContent = d.entry.partOfSpeech;
				entry.appendChild(pos);
			}
			const text = document.createElement('div');
			text.className = 'def-text';
			text.textContent = d.entry.definition;
			entry.appendChild(text);
		} else {
			const missing = document.createElement('div');
			missing.className = 'def-missing';
			missing.textContent = 'No definition available';
			entry.appendChild(missing);
		}
		defList.appendChild(entry);
	}
	defOverlay.classList.remove('hidden');
}

lastBox.addEventListener('click', openDefinitions);
defOverlay.addEventListener('click', (e) => {
	if (e.target === defOverlay) defOverlay.classList.add('hidden');
});

async function refreshPreview() {
	if (state.pending.length === 0) {
		state.preview = null;
		renderBoard();
		renderInfoPanel();
		return;
	}
	state.preview = null;
	renderBoard();
	const mySeq = ++validateSeq;
	const placements = state.pending.map(p => ({
		row: p.row, col: p.col, letter: p.letter, blank: p.blank,
	}));
	try {
		const r = await fetch('/validate', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ ...authParams(), placements }),
		});
		const data = await r.json();
		if (mySeq !== validateSeq) return;
		state.preview = {
			words: data.words || [],
			score: data.score || 0,
			status: data.status || '',
		};
		renderBoard();
		renderInfoPanel();
	} catch (e) {}
}

async function confirmSwap() {
	if (state.swapSelection.size === 0) return;
	const tiles = Array.from(state.swapSelection).map(id => {
		const item = state.rack.find(r => r.id === id);
		return item ? item.letter : null;
	}).filter(l => l !== null);
	const r = await fetch('/swap', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ ...authParams(), tiles }),
	});
	const data = await r.json();
	if (!r.ok) {
		showMessage(data.error, 'error');
		return;
	}
	state.swapMode = false;
	state.swapSelection.clear();
	clearMessage();
	await loadState();
}

document.getElementById('btn-submit').addEventListener('click', async () => {
	if (state.swapMode) {
		if (state.swapSelection.size === 0) {
			showMessage('Tap tiles in your rack to swap', 'error');
			return;
		}
		await confirmSwap();
		return;
	}
	if (state.pending.length === 0) {
		showMessage('Drag tiles onto the board first', 'error');
		return;
	}
	zoomOut();
	showMessage('Checking word...', 'success');
	const placedCount = state.pending.length;
	const placements = state.pending.map(p => ({
		row: p.row, col: p.col, letter: p.letter, blank: p.blank,
	}));
	const r = await fetch('/submit', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ ...authParams(), placements }),
	});
	const data = await r.json();
	if (!r.ok) {
		showMessage(data.error || 'Invalid move', 'error');
		return;
	}
	state.pending = [];
	clearMessage();
	if (placedCount === 7) soundBingo();
	else soundPlayWord();
	await loadState();
});

document.getElementById('btn-recall').addEventListener('click', () => {
	if (state.swapMode) {
		state.swapMode = false;
		state.swapSelection.clear();
		clearMessage();
		renderRack();
		renderBoard();
		return;
	}
	if (state.pending.length > 0) soundRecall();
	state.pending = [];
	state.preview = null;
	zoomOut();
	clearMessage();
	renderBoard();
	renderRack();
	renderInfoPanel();
	sharePendingSoon();
});

document.getElementById('btn-menu-pass').addEventListener('click', async () => {
	if (!confirm('Pass your turn?')) return;
	menuOverlay.classList.add('hidden');
	await fetch('/pass', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(authParams()),
	});
	await loadState();
});

const btnMenuHint = document.getElementById('btn-menu-hint');
if (btnMenuHint) {
	btnMenuHint.addEventListener('click', async () => {
		if ((state.hintTokens || 0) < 1) return;
		if (state.current !== state.mySlot) {
			showMessage('You can only use a hint on your turn', 'error');
			return;
		}
		menuOverlay.classList.add('hidden');
		try {
			const r = await fetch('/hint', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(authParams()),
			});
			const d = await r.json();
			if (!r.ok) {
				showMessage(d.error || 'Hint failed', 'error');
				return;
			}
			if (d.refunded) {
				showMessage(d.error, 'error');
				await loadState();
				return;
			}
			state.hintCells = d.cells || [];
			if (typeof d.tokens_remaining === 'number') state.hintTokens = d.tokens_remaining;
			renderBoard();
			renderTurnState();
			showMessage('Hint: your best play goes on the highlighted cells', 'success');
		} catch (e) {
			showMessage('Network error', 'error');
		}
	});
}

document.getElementById('btn-menu-challenge').addEventListener('click', async () => {
	if (!state.canChallenge) {
		showMessage('Nothing to challenge', 'error');
		return;
	}
	if (!confirm('Challenge the last play? You lose your turn if all words are valid.')) return;
	menuOverlay.classList.add('hidden');
	try {
		const r = await fetch('/challenge', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(authParams()),
		});
		const data = await r.json();
		if (!r.ok) {
			showMessage(data.error || 'Challenge failed', 'error');
		} else if (data.result === 'won') {
			const bad = (data.invalid_words || []).join(', ');
			showMessage(`Challenge won — invalid: ${bad}`, 'success');
		} else {
			showMessage('Challenge lost — turn skipped', 'error');
		}
	} catch (e) {
		showMessage('Challenge failed', 'error');
	}
	await loadState();
});

(function setupHistoryButton() {
	const btn = document.getElementById('btn-history');
	const overlay = document.getElementById('history-overlay');
	const list = document.getElementById('history-list');
	const closeBtn = document.getElementById('btn-history-close');
	if (!btn || !overlay || !list) return;
	function open() {
		const items = state.history || [];
		if (items.length === 0) {
			list.innerHTML = '<div class="history-empty">No moves yet.</div>';
		} else {
			list.innerHTML = items.slice().reverse().map((h, idx) => {
				const n = items.length - idx;
				const name = h.name || `P${(h.slot ?? 0) + 1}`;
				let detail = '';
				if (h.action === 'play') {
					const wordsText = (h.words || []).join(', ');
					const sign = h.score >= 0 ? '+' : '';
					detail = `<span class="history-words">${escapeHtml(wordsText)}</span><span class="history-points">${sign}${h.score}</span>`;
				} else if (h.action === 'pass') {
					detail = `<span class="history-action">passed</span>`;
				} else if (h.action === 'swap') {
					detail = `<span class="history-action">swapped tiles</span>`;
				} else if (h.action === 'forfeit') {
					detail = `<span class="history-action">forfeited</span>`;
				} else {
					detail = `<span class="history-action">${escapeHtml(h.action)}</span>`;
				}
				return `<div class="history-row history-slot-${h.slot}">
					<span class="history-num">#${n}</span>
					<span class="history-name">${escapeHtml(name)}</span>
					${detail}
					<span class="history-total">= ${h.total_after}</span>
				</div>`;
			}).join('');
		}
		overlay.classList.remove('hidden');
	}
	btn.addEventListener('click', open);
	if (closeBtn) closeBtn.addEventListener('click', () => overlay.classList.add('hidden'));
	overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.classList.add('hidden'); });
})();

const btnMenuLobby = document.getElementById('btn-menu-lobby');
if (btnMenuLobby) {
	btnMenuLobby.addEventListener('click', () => {
		menuOverlay.classList.add('hidden');
		leaveActiveRoomKeepCreds();
	});
}

document.getElementById('btn-menu-forfeit').addEventListener('click', async () => {
	if (!confirm('Leave this game? If it is still active, your opponent wins.')) return;
	menuOverlay.classList.add('hidden');
	try {
		await fetch('/forfeit', {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(authParams()),
		});
	} catch (e) {}
	if (typeof stopRealtime === 'function') stopRealtime();
	clearCreds();
	history.replaceState(null, '', '/');
	if (typeof showLanding === 'function') showLanding();
	else window.location.reload();
});

document.getElementById('btn-menu-swap').addEventListener('click', () => {
	if (state.bagCount < 7) {
		showMessage('Cannot swap: fewer than 7 tiles left in bag', 'error');
		return;
	}
	menuOverlay.classList.add('hidden');
	state.swapMode = true;
	state.swapSelection.clear();
	state.pending = [];
	state.preview = null;
	showMessage('Tap tiles to swap, then hit ✓ to confirm', 'success');
	renderBoard();
	renderRack();
});

function syncAppPadding() {
	const rw = document.querySelector('.rack-wrap');
	if (!rw) return;
	requestAnimationFrame(() => {
		const h = rw.getBoundingClientRect().height;
		document.documentElement.style.setProperty('--rack-h', h + 'px');
	});
}

const rackWrapEl = document.querySelector('.rack-wrap');
if (rackWrapEl && window.ResizeObserver) {
	new ResizeObserver(syncAppPadding).observe(rackWrapEl);
}
window.addEventListener('resize', syncAppPadding);
window.addEventListener('orientationchange', syncAppPadding);

let outlineReflowQueued = false;
function queueOutlineReflow() {
	if (outlineReflowQueued) return;
	outlineReflowQueued = true;
	requestAnimationFrame(() => {
		outlineReflowQueued = false;
		renderBoard();
	});
}
if (boardEl && window.ResizeObserver) {
	new ResizeObserver(queueOutlineReflow).observe(boardEl);
}
window.addEventListener('resize', queueOutlineReflow);
window.addEventListener('orientationchange', queueOutlineReflow);

const landingOverlay = document.getElementById('landing-overlay');
const joinOverlay = document.getElementById('join-overlay');
const nameOverlay = document.getElementById('name-overlay');
const joinInput = document.getElementById('join-room-input');
const joinError = document.getElementById('join-error');
const landingNameInput = document.getElementById('landing-name-input');
const landingError = document.getElementById('landing-error');
const nameInput = document.getElementById('name-input');
const nameError = document.getElementById('name-error');

function showLanding() {
	landingOverlay.classList.remove('hidden');
	joinOverlay.classList.add('hidden');
	nameOverlay.classList.add('hidden');
	landingError.textContent = '';
	landingNameInput.value = state.playerName || '';
	const urlRid = getUrlRoomId();
	const watch = isUrlWatchMode();
	document.body.classList.toggle('join-via-url', !!urlRid);
	document.body.classList.toggle('watch-via-url', !!(urlRid && watch));
	const createBtn = document.getElementById('btn-create-room');
	if (createBtn) {
		if (urlRid && watch) createBtn.textContent = `Watch ${urlRid}`;
		else if (urlRid) createBtn.textContent = `Join ${urlRid}`;
		else createBtn.textContent = 'Create Room';
	}
	renderActiveRooms();
	refreshOnlineCount();
	if (landingOnlineTimer) clearInterval(landingOnlineTimer);
	landingOnlineTimer = setInterval(refreshOnlineCount, 5000);
}

async function renderActiveRooms() {
	const el = document.getElementById('active-rooms');
	if (!el) return;
	const map = loadRoomsMap();
	const urlRid = getUrlRoomId();
	// Finished rooms stay on the server for rematches, so ask which rooms are still in play: drop the ones that are gone, hide the ones that are over.
	const live = await Promise.all(Object.keys(map).filter(r => r !== urlRid).map(async (r) => {
		try {
			const d = await (await fetch(`/room_exists/${encodeURIComponent(r)}`)).json();
			if (!d.exists) { delRoomCred(r); return null; }
			return d.game_over ? null : r;
		} catch (e) {
			return r;
		}
	}));
	const rids = live.filter(Boolean);
	if (rids.length === 0) {
		el.classList.add('hidden');
		el.innerHTML = '';
		return;
	}
	rids.sort((a, b) => (map[b].lastSeen || 0) - (map[a].lastSeen || 0));
	const buttons = rids.map(r => `<button class="active-room-btn" data-rid="${r}">${r}</button>`).join('');
	el.classList.remove('hidden');
	el.innerHTML = `<span class="active-rooms-label">Active games</span><div class="active-rooms-list">${buttons}</div>`;
	el.querySelectorAll('.active-room-btn').forEach(b => {
		b.addEventListener('click', async () => {
			const r = b.dataset.rid;
			const ok = await switchToRoom(r);
			if (!ok) renderActiveRooms();
		});
	});
}

let landingOnlineTimer = null;
async function refreshOnlineCount() {
	const el = document.getElementById('landing-online');
	if (!el) return;
	try {
		const r = await fetch('/online');
		const d = await r.json();
		const p = d.players || 0;
		const rooms = d.rooms || 0;
		el.textContent = p === 0
			? 'No one online right now'
			: `${p} player${p === 1 ? '' : 's'} online in ${rooms} game${rooms === 1 ? '' : 's'}`;
	} catch (e) {}
}

function hideAllRoomOverlays() {
	if (landingOnlineTimer) { clearInterval(landingOnlineTimer); landingOnlineTimer = null; }
	landingOverlay.classList.add('hidden');
	joinOverlay.classList.add('hidden');
	nameOverlay.classList.add('hidden');
}

function requireLandingName() {
	const name = cleanName(landingNameInput.value);
	if (!name) {
		landingError.textContent = 'Enter a name (letters only)';
		landingNameInput.focus();
		return null;
	}
	landingError.textContent = '';
	saveName(name);
	return name;
}

let selectedMaxPlayers = 2;
document.querySelectorAll('.players-opt').forEach(btn => {
	btn.addEventListener('click', () => {
		document.querySelectorAll('.players-opt').forEach(b => b.classList.remove('selected'));
		btn.classList.add('selected');
		selectedMaxPlayers = parseInt(btn.dataset.players, 10) || 2;
	});
});

// acid-arcade-db profile picture next to an account name (empty for guests and standalone mode)
function avatarHtml(userId) {
	if (!window.ARCADE_PUBLIC_URL || userId === null || userId === undefined) return '';
	return `<img class="avatar" src="${window.ARCADE_PUBLIC_URL}/avatar/${encodeURIComponent(userId)}" alt="">`;
}

function saveAuth(token, username, userId) {
	state.authToken = token;
	state.authUsername = username;
	if (userId !== undefined) state.authUserId = userId;
	localStorage.setItem('scrabble_auth_token', token);
	localStorage.setItem('scrabble_auth_username', username);
	refreshAuthStatus();
}

function clearAuth() {
	state.authToken = null;
	state.authUsername = null;
	state.authUserId = null;
	localStorage.removeItem('scrabble_auth_token');
	localStorage.removeItem('scrabble_auth_username');
	refreshAuthStatus();
}

function refreshAuthStatus() {
	const txt = document.getElementById('auth-status-text');
	const showAuth = document.getElementById('btn-show-auth');
	const showProf = document.getElementById('btn-show-profile');
	const logoutBtn = document.getElementById('btn-logout');
	const accountLink = document.getElementById('link-account-settings');
	const nameInput = document.getElementById('landing-name-input');
	if (!txt) return;
	if (state.authUsername) {
		txt.innerHTML = `Signed in as ${avatarHtml(state.authUserId)}${escapeHtml(state.authUsername)}`;
		showAuth.classList.add('hidden');
		showProf.classList.remove('hidden');
		logoutBtn.classList.remove('hidden');
		if (window.ARCADE_PUBLIC_URL) {
			accountLink.href = `${window.ARCADE_PUBLIC_URL}/account`;
			accountLink.classList.remove('hidden');
		}
		if (nameInput) {
			nameInput.value = state.authUsername.toUpperCase().slice(0, 9);
			nameInput.disabled = true;
		}
	} else {
		const guestKey = 'scrabble_guest_name';
		let guest = localStorage.getItem(guestKey);
		if (!guest || guest !== cleanName(guest)) {
			guest = 'GUEST' + Array.from({ length: 4 }, () => String.fromCharCode(65 + Math.floor(Math.random() * 26))).join('');
			localStorage.setItem(guestKey, guest);
		}
		state.playerName = guest;
		localStorage.setItem('scrabble_player_name', guest);
		txt.textContent = `Playing as ${guest}`;
		showAuth.classList.remove('hidden');
		showProf.classList.add('hidden');
		logoutBtn.classList.add('hidden');
		accountLink.classList.add('hidden');
		if (nameInput) {
			nameInput.value = guest;
			nameInput.disabled = false;
		}
	}
}

(function setupAuthUI() {
	const overlay = document.getElementById('auth-overlay');
	const tabLogin = document.getElementById('auth-tab-login');
	const tabSignup = document.getElementById('auth-tab-signup');
	const usernameInput = document.getElementById('auth-username');
	const passwordInput = document.getElementById('auth-password');
	const captchaRow = document.getElementById('auth-captcha-row');
	const captchaImg = document.getElementById('auth-captcha-image');
	const captchaA = document.getElementById('auth-captcha-answer');
	const captchaRefresh = document.getElementById('auth-captcha-refresh');
	const errEl = document.getElementById('auth-error');
	const submitBtn = document.getElementById('btn-auth-submit');
	const cancelBtn = document.getElementById('btn-auth-cancel');
	const showAuthBtn = document.getElementById('btn-show-auth');
	const logoutBtn = document.getElementById('btn-logout');
	const showProfBtn = document.getElementById('btn-show-profile');
	const profOverlay = document.getElementById('profile-overlay');
	const profContent = document.getElementById('profile-content');
	const profUsername = document.getElementById('profile-username');
	const profClose = document.getElementById('btn-profile-close');
	let mode = 'login';
	let captchaId = null;
	if (!overlay) return;

	function setMode(m) {
		mode = m;
		tabLogin.classList.toggle('selected', m === 'login');
		tabSignup.classList.toggle('selected', m === 'signup');
		submitBtn.textContent = m === 'login' ? 'Log in' : 'Sign up';
		captchaRow.classList.toggle('hidden', m !== 'signup');
		passwordInput.autocomplete = m === 'login' ? 'current-password' : 'new-password';
		errEl.textContent = '';
		if (m === 'signup') loadCaptcha();
	}

	async function loadCaptcha() {
		try {
			const r = await fetch('/auth/captcha');
			const d = await r.json();
			captchaId = d.captcha_id;
			captchaImg.innerHTML = d.svg;
			captchaA.value = '';
		} catch (e) {
			errEl.textContent = 'Could not load captcha';
		}
	}

	async function submit() {
		const username = (usernameInput.value || '').trim();
		const password = passwordInput.value || '';
		if (!username || !password) { errEl.textContent = 'Username and password required'; return; }
		let url, body;
		if (mode === 'signup') {
			if (!/^[A-Za-z0-9_]{3,16}$/.test(username)) { errEl.textContent = 'Username: 3-16 letters/digits/underscores'; return; }
			if (password.length < 6) { errEl.textContent = 'Password must be at least 6 characters'; return; }
			url = '/auth/signup';
			body = { username, password, captcha_id: captchaId, captcha_answer: (captchaA.value || '').trim() };
		} else {
			url = '/auth/login';
			body = { username, password };
		}
		try {
			const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
			const d = await r.json();
			if (!r.ok) {
				errEl.textContent = d.error || 'Failed';
				announce(errEl.textContent);
				if (mode === 'signup') loadCaptcha();
				return;
			}
			saveAuth(d.auth_token, d.username, d.user_id);
			overlay.classList.add('hidden');
			usernameInput.value = ''; passwordInput.value = '';
			syncActiveRoomsFromAccount();
		} catch (e) {
			errEl.textContent = 'Network error';
		}
	}

	tabLogin.addEventListener('click', () => setMode('login'));
	tabSignup.addEventListener('click', () => setMode('signup'));
	if (captchaRefresh) captchaRefresh.addEventListener('click', loadCaptcha);
	const pwToggle = document.getElementById('auth-password-toggle');
	if (pwToggle) {
		pwToggle.addEventListener('click', () => {
			const shown = passwordInput.type === 'text';
			passwordInput.type = shown ? 'password' : 'text';
			pwToggle.classList.toggle('revealed', !shown);
			pwToggle.title = shown ? 'Show password' : 'Hide password';
			pwToggle.setAttribute('aria-label', pwToggle.title);
		});
	}
	submitBtn.addEventListener('click', submit);
	passwordInput.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
	captchaA.addEventListener('keydown', e => { if (e.key === 'Enter') submit(); });
	cancelBtn.addEventListener('click', () => { overlay.classList.add('hidden'); });
	showAuthBtn.addEventListener('click', () => { setMode('login'); overlay.classList.remove('hidden'); usernameInput.focus(); });
	logoutBtn.addEventListener('click', async () => {
		try { await fetch('/auth/logout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ auth_token: state.authToken }) }); } catch (e) {}
		clearAuth();
	});
	showProfBtn.addEventListener('click', async () => {
		if (!state.authToken) return;
		try {
			const r = await fetch('/auth/stats', { headers: { 'X-Auth-Token': state.authToken } });
			const d = await r.json();
			if (!r.ok) { profContent.textContent = d.error || 'Failed'; }
			else {
				profUsername.innerHTML = avatarHtml(d.user_id) + escapeHtml(d.username);
				const wl = d.wins, ls = d.losses, dr = d.draws, gp = d.games_played;
				const avg = d.total_plays > 0 ? Math.round(d.total_play_score / d.total_plays) : 0;
				const hi = d.highest_play_words.join(', ');
				const mp = d.most_words_play_words.join(', ');
				const tw = d.top_words.map(w => `<div class="profile-row"><span>${w.word}</span><span>${w.count}</span></div>`).join('');
				const streakCur = d.current_win_streak || 0;
				const streakBest = d.longest_win_streak || 0;
				const tokens = d.hint_tokens || 0;
				const capT = d.hint_token_cap || 5;
				const nextTok = d.wins_to_next_token || 0;
				profContent.innerHTML = `
					<div class="profile-grid">
						<div class="profile-row"><span>Games</span><span>${gp}</span></div>
						<div class="profile-row"><span>Wins</span><span>${wl}</span></div>
						<div class="profile-row"><span>Losses</span><span>${ls}</span></div>
						<div class="profile-row"><span>Draws</span><span>${dr}</span></div>
						<div class="profile-row"><span>Current win streak</span><span>${streakCur}</span></div>
						<div class="profile-row"><span>Longest win streak</span><span>${streakBest}</span></div>
						<div class="profile-row"><span>Hint tokens</span><span>${tokens} / ${capT}${nextTok ? ` (${nextTok} more wins to next)` : ''}</span></div>
						<div class="profile-row"><span>Total plays</span><span>${d.total_plays}</span></div>
						<div class="profile-row"><span>Avg play score</span><span>${avg}</span></div>
						<div class="profile-row"><span>Highest play</span><span>${d.highest_play_score}${hi ? ` (${hi})` : ''}</span></div>
						<div class="profile-row"><span>Longest word</span><span>${d.longest_word || '-'}</span></div>
						<div class="profile-row"><span>Most words/play</span><span>${d.most_words_in_play}${mp ? ` (${mp})` : ''}</span></div>
					</div>
					<h3 class="profile-section">Most played words</h3>
					<div class="profile-grid">${tw || '<div class="profile-row"><span>-</span></div>'}</div>
				`;
			}
			profOverlay.classList.remove('hidden');
		} catch (e) { profContent.textContent = 'Network error'; profOverlay.classList.remove('hidden'); }
	});
	profClose.addEventListener('click', () => profOverlay.classList.add('hidden'));

	// Signed in on another acid-arcade site of this domain? Swap the shared cookie for a session here (once per page load)
	let ssoTried = false;
	async function trySso() {
		if (!window.ARCADE_PUBLIC_URL || ssoTried) return false;
		ssoTried = true;
		try {
			const r = await fetch('/auth/sso');
			if (!r.ok) return false;
			const d = await r.json();
			saveAuth(d.auth_token, d.username, d.user_id);
			syncActiveRoomsFromAccount();
			return true;
		} catch (e) {
			return false;
		}
	}

	// Verify session at startup
	if (state.authToken) {
		fetch('/auth/me', { headers: { 'X-Auth-Token': state.authToken } })
			.then(r => r.json())
			.then(async d => {
				if (d.user) {
					saveAuth(state.authToken, d.user.username, d.user.user_id);
					syncActiveRoomsFromAccount();
				} else if (!window.ARCADE_PUBLIC_URL) clearAuth();
				else if (d.user === null && !(await trySso())) clearAuth();
			})
			.catch(() => {});
	} else {
		refreshAuthStatus();
		trySso();
	}
})();

(function setupVsComputer() {
	const cb = document.getElementById('vs-computer-checkbox');
	const playersPicker = document.getElementById('players-picker');
	const diffPicker = document.getElementById('difficulty-picker');
	const diffSlider = document.getElementById('difficulty-slider');
	const diffValue = document.getElementById('difficulty-value');
	const stackerWrap = document.getElementById('stacker-toggle-wrap');
	const stackerCb = document.getElementById('stacker-checkbox');
	if (!cb || !playersPicker || !diffPicker) return;
	const otherToggles = ['speed-checkbox', 'challenge-checkbox', 'transparent-checkbox']
		.map(id => document.getElementById(id)).filter(Boolean);
	function refresh() {
		const on = cb.checked;
		// Players picker stays visible in AI mode — 2P = you vs AI, 3-4P = humans + AI joining together
		if (stackerWrap) stackerWrap.classList.toggle('hidden', !on);
		if (!on && stackerCb) stackerCb.checked = false;
		const stack = !!(on && stackerCb && stackerCb.checked);
		// In stacker mode, difficulty is locked at 100 so we hide the slider entirely
		diffPicker.classList.toggle('hidden', !on || stack);
		otherToggles.forEach(t => {
			t.disabled = on;
			const wrap = t.closest('.speed-toggle');
			if (wrap) wrap.classList.toggle('disabled', on);
			if (on) t.checked = false;
		});
	}
	cb.addEventListener('change', refresh);
	if (stackerCb) stackerCb.addEventListener('change', refresh);
	if (diffSlider) {
		diffSlider.addEventListener('input', () => { diffValue.textContent = diffSlider.value; });
	}
	refresh();
})();

let selectedMode = 'classic';
document.querySelectorAll('.mode-opt').forEach(btn => {
	btn.addEventListener('click', () => {
		document.querySelectorAll('.mode-opt').forEach(b => b.classList.remove('selected'));
		btn.classList.add('selected');
		selectedMode = btn.dataset.mode;
	});
});

document.getElementById('btn-create-room').addEventListener('click', async () => {
	const name = requireLandingName();
	if (!name) return;
	const urlRid = getUrlRoomId();
	if (urlRid) {
		const ok = isUrlWatchMode()
			? await watchByRoomId(urlRid, name)
			: await joinByRoomId(urlRid, name);
		if (ok) return;
		history.replaceState(null, '', '/');
		document.body.classList.remove('join-via-url');
		document.body.classList.remove('watch-via-url');
		const createBtn = document.getElementById('btn-create-room');
		if (createBtn) createBtn.textContent = 'Create Room';
		return;
	}
	const speed = document.getElementById('speed-checkbox').checked;
	const challengeMode = document.getElementById('challenge-checkbox').checked;
	const transparentMode = document.getElementById('transparent-checkbox').checked;
	const vsComputer = document.getElementById('vs-computer-checkbox').checked;
	const stackerOn = !!(document.getElementById('stacker-checkbox') && document.getElementById('stacker-checkbox').checked);
	const aiDifficulty = parseInt(document.getElementById('difficulty-slider').value, 10) || 50;
	const body = vsComputer
		? { name, mode: selectedMode, speed: false, challenge_mode: false, transparent_mode: false, max_players: selectedMaxPlayers, vs_computer: true, ai_difficulty: stackerOn ? 100 : aiDifficulty, ai_style: stackerOn ? 'stacker' : 'score' }
		: { name, mode: selectedMode, speed, challenge_mode: challengeMode, transparent_mode: transparentMode, max_players: selectedMaxPlayers };
	if (state.authToken) body.auth_token = state.authToken;
	const r = await fetch('/create_room', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body),
	});
	const data = await r.json();
	if (!r.ok) {
		landingError.textContent = data.error || 'Could not create room';
		announce(landingError.textContent);
		return;
	}
	saveCreds(data.room_id, data.player_token);
	history.replaceState(null, '', '/' + data.room_id);
	hideAllRoomOverlays();
	await loadState();
	startRealtime();
});

async function joinByRoomId(rid, name) {
	const body = { room_id: rid, name };
	if (state.authToken) body.auth_token = state.authToken;
	const r = await fetch('/join_room', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(body),
	});
	const data = await r.json();
	if (!r.ok) {
		landingError.textContent = `${data.error || 'Could not join'} (${rid}) — tap Create Room to start a new game`;
		announce(landingError.textContent);
		return false;
	}
	saveCreds(data.room_id, data.player_token);
	history.replaceState(null, '', '/' + data.room_id);
	hideAllRoomOverlays();
	await loadState();
	startRealtime();
	return true;
}

async function watchByRoomId(rid, name) {
	const r = await fetch('/watch_room', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ room_id: rid, name }),
	});
	const data = await r.json();
	if (!r.ok) {
		landingError.textContent = `${data.error || 'Could not watch'} (${rid})`;
		announce(landingError.textContent);
		return false;
	}
	saveCreds(data.room_id, data.player_token);
	history.replaceState(null, '', '/' + data.room_id + '?watch=true');
	hideAllRoomOverlays();
	await loadState();
	startRealtime();
	return true;
}

function getUrlRoomId() {
	const m = window.location.pathname.match(/^\/([a-z0-9]{4})\/?$/i);
	if (!m) return null;
	const rid = m[1].toLowerCase();
	if (/[01oli]/.test(rid)) return null;
	return rid;
}

function isUrlWatchMode() {
	const params = new URLSearchParams(window.location.search);
	return params.get('watch') === 'true';
}

const chatOverlay = document.getElementById('chat-overlay');
const chatMessagesEl = document.getElementById('chat-messages');
const chatInput = document.getElementById('chat-input');
const endgameOverlay = document.getElementById('endgame-overlay');
const endgameTitle = document.getElementById('endgame-title');
const endgameBody = document.getElementById('endgame-body');

let partyTimer = null;
let partyMode = localStorage.getItem('scrabble_party') === '1';

function partyTick() {
	if (!partyMode) return;
	const hue = Math.floor(Math.random() * 360);
	document.documentElement.style.setProperty('--party-hue', hue + 'deg');
	const next = 800 + Math.random() * 2200;
	partyTimer = setTimeout(partyTick, next);
}

function setPartyMode(on) {
	partyMode = on;
	localStorage.setItem('scrabble_party', on ? '1' : '0');
	if (on) {
		document.documentElement.classList.add('party-mode');
		partyTick();
	} else {
		document.documentElement.classList.remove('party-mode');
		document.documentElement.style.setProperty('--party-hue', '0deg');
		if (partyTimer) { clearTimeout(partyTimer); partyTimer = null; }
	}
}

if (partyMode) setPartyMode(true);

function fmtDuration(secs) {
	if (!secs) return '0s';
	const m = Math.floor(secs / 60);
	const s = secs % 60;
	if (m === 0) return `${s}s`;
	return `${m}m ${s}s`;
}

function renderChat() {
	if (!state.chat || state.chat.length === 0) {
		chatMessagesEl.innerHTML = '<div class="chat-empty">No messages yet. Say hi!</div>';
		return;
	}
	const html = state.chat.map(m => {
		const cls = m.slot === state.mySlot ? 'mine' : 'theirs';
		const safeName = avatarHtml(m.spectator ? null : state.userIds[m.slot]) + escapeHtml(m.name);
		const safeText = escapeHtml(m.text);
		return `<div class="chat-msg ${cls}"><span class="chat-name">${safeName}:</span>${safeText}</div>`;
	}).join('');
	chatMessagesEl.innerHTML = html;
	chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
}

function escapeHtml(s) {
	return String(s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' })[c]);
}

function openChat() {
	state.chatOpen = true;
	if (state.chat.length) {
		state.lastSeenChatId = state.chat[state.chat.length - 1].id;
		const key = chatSeenKey();
		if (key) localStorage.setItem(key, String(state.lastSeenChatId));
	}
	state.unreadChat = 0;
	renderChatBadge();
	renderChat();
	chatOverlay.classList.remove('hidden');
	setTimeout(() => chatInput.focus(), 50);
}

function closeChat() {
	state.chatOpen = false;
	chatOverlay.classList.add('hidden');
}

document.getElementById('btn-menu-chat').addEventListener('click', () => {
	menuOverlay.classList.add('hidden');
	openChat();
});

chatOverlay.addEventListener('click', (e) => {
	if (e.target === chatOverlay) closeChat();
});

document.getElementById('btn-chat-send').addEventListener('click', async () => {
	const text = (chatInput.value || '').trim().slice(0, 100);
	if (!text) return;
	const btn = document.getElementById('btn-chat-send');
	btn.disabled = true;
	const r = await fetch('/chat', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ ...authParams(), text }),
	});
	if (r.ok) {
		chatInput.value = '';
		await loadState(true);
		btn.disabled = false;
	} else {
		const data = await r.json().catch(() => ({}));
		if (r.status === 429 && data.retry_after) {
			const ms = Math.max(200, Math.ceil(data.retry_after * 1000));
			btn.textContent = `Wait ${data.retry_after}s`;
			setTimeout(() => { btn.disabled = false; btn.textContent = 'Send'; }, ms);
		} else {
			btn.disabled = false;
		}
	}
});

chatInput.addEventListener('keydown', e => {
	if (e.key === 'Enter') document.getElementById('btn-chat-send').click();
});

function showEndGame() {
	const f = state.finalSummary;
	if (!f) return;
	let title = 'Game Over';
	let winnerLine = '';
	if (f.winner === null) {
		title = f.reason === 'three_passes' ? 'Game ended — 3 passes' : 'Draw';
		winnerLine = `<div class="endgame-winner">It's a draw</div>`;
	} else {
		const winName = f.names[f.winner] || `P${f.winner + 1}`;
		if (state.mySlot !== null) title = f.winner === state.mySlot ? 'You won!' : 'You lost';
		winnerLine = `<div class="endgame-winner">${escapeHtml(winName)} wins</div>`;
	}
	endgameTitle.textContent = title;
	const reasonMap = {
		tiles_out: 'Tiles exhausted',
		three_passes: '3 passes in a row',
		timeout: '24h inactivity',
		inactive: '3 missed turns in a row',
		turn_limit: '5 turns each — final score',
		forfeit: 'Forfeit',
	};
	const reasonText = reasonMap[f.reason] || f.reason;
	const rowList = f.scores.map((sc, i) => {
		const cls = f.winner === null ? '' : (f.winner === i ? 'win' : 'lose');
		return `<div class="endgame-row"><span class="lbl">${avatarHtml(state.userIds[i])}${escapeHtml(f.names[i] || 'P' + (i + 1))}</span><span class="val ${cls}">${sc}</span></div>`;
	});
	rowList.push(`<div class="endgame-row"><span class="lbl">Moves</span><span class="val">${(f.moves || []).reduce((a, b) => a + b, 0)}</span></div>`);
	if (f.challenge_mode && Array.isArray(f.bluffs)) {
		const bonus = Array.isArray(f.bluff_bonus) ? f.bluff_bonus : [];
		for (let i = 0; i < f.bluffs.length; i++) {
			const b = bonus[i] || 0;
			const bonusText = b > 0 ? ` (+${b}pts)` : '';
			rowList.push(`<div class="endgame-row"><span class="lbl">${escapeHtml(f.names[i] || 'P' + (i + 1))} bluffs</span><span class="val">${f.bluffs[i]}${bonusText}</span></div>`);
		}
	}
	rowList.push(`<div class="endgame-row"><span class="lbl">Duration</span><span class="val">${fmtDuration(f.duration_seconds)}</span></div>`);
	rowList.push(`<div class="endgame-row"><span class="lbl">Ended by</span><span class="val">${reasonText}</span></div>`);
	const rows = rowList.join('');
	endgameBody.innerHTML = winnerLine + rows;
	endgameOverlay.classList.remove('hidden');
	updateRematchStatus();
}

endgameOverlay.addEventListener('click', (e) => {
	if (e.target === endgameOverlay) endgameOverlay.classList.add('hidden');
});

document.getElementById('btn-rematch').addEventListener('click', async () => {
	const btn = document.getElementById('btn-rematch');
	btn.disabled = true;
	const r = await fetch('/rematch', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(authParams()),
	});
	if (!r.ok) {
		btn.disabled = false;
		return;
	}
	await loadState();
	updateRematchStatus();
});

const btnRematchDecline = document.getElementById('btn-rematch-decline');
if (btnRematchDecline) {
	btnRematchDecline.addEventListener('click', async () => {
		btnRematchDecline.disabled = true;
		try {
			await fetch('/rematch', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ ...authParams(), decline: true }),
			});
		} catch (e) {}
		btnRematchDecline.disabled = false;
		endgameOverlay.classList.add('hidden');
		leaveActiveRoomKeepCreds();
	});
}

const btnEndgameQuit = document.getElementById('btn-endgame-quit');
if (btnEndgameQuit) {
	btnEndgameQuit.addEventListener('click', () => {
		endgameOverlay.classList.add('hidden');
		leaveActiveRoomKeepCreds();
	});
}

const leaderboardOverlay = document.getElementById('leaderboard-overlay');
const leaderboardContent = document.getElementById('leaderboard-content');

function fmtDate(ts) {
	if (!ts) return '';
	const d = new Date(ts * 1000);
	return d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function section(title, html) {
	return `<div class="lb-section"><h3>${title}</h3>${html}</div>`;
}

function renderModeColumn(data) {
	let html = section('Total games played', `<div class="lb-total">${data.total_games || 0}</div>`);
	if (data.top_play) {
		const p = data.top_play;
		html += section('Top scoring play', `
			<div class="lb-row"><span class="lb-word">${p.words.join(', ')}</span><span class="lb-score">+${p.score}</span></div>
			<div class="lb-row"><span class="lb-player">${avatarHtml(p.user_id)}${escapeHtml(p.player)}</span><span class="lb-meta">${fmtDate(p.at)}</span></div>
		`);
	} else {
		html += section('Top scoring play', '<div class="lb-empty">No plays yet</div>');
	}
	if (data.top_game) {
		const g = data.top_game;
		html += section('Highest scoring game', `
			<div class="lb-row"><span class="lb-player">${avatarHtml(g.user_id)}${escapeHtml(g.player)}</span><span class="lb-score">${g.score}</span></div>
			<div class="lb-row"><span class="lb-meta">vs ${avatarHtml(g.opponent_user_id)}${escapeHtml(g.opponent)} (${g.opponent_score})</span><span class="lb-meta">${fmtDate(g.at)}</span></div>
		`);
	} else {
		html += section('Highest scoring game', '<div class="lb-empty">No games finished yet</div>');
	}
	if (data.longest_word) {
		const l = data.longest_word;
		html += section('Longest word played', `
			<div class="lb-row"><span class="lb-word">${escapeHtml(l.word)}</span><span class="lb-meta">${l.word.length} letters</span></div>
			<div class="lb-row"><span class="lb-player">${avatarHtml(l.user_id)}${escapeHtml(l.player)}</span><span class="lb-meta">${fmtDate(l.at)}</span></div>
		`);
	} else {
		html += section('Longest word played', '<div class="lb-empty">No words yet</div>');
	}
	if (data.top_words && data.top_words.length) {
		const rows = data.top_words.map(w =>
			`<div class="lb-row"><span class="lb-word">${w.word}</span><span class="lb-meta">×${w.count}</span></div>`
		).join('');
		html += section('Most played words', rows);
	} else {
		html += section('Most played words', '<div class="lb-empty">No words yet</div>');
	}
	return html;
}

function renderLeaderboard(data) {
	const classic = data.classic || { total_games: 0 };
	const random = data.random || { total_games: 0 };
	leaderboardContent.innerHTML = `
		<div class="lb-columns">
			<div class="lb-column"><h3 class="lb-mode-title">Classic</h3>${renderModeColumn(classic)}</div>
			<div class="lb-column"><h3 class="lb-mode-title">Random</h3>${renderModeColumn(random)}</div>
		</div>
	`;
}

document.getElementById('btn-show-leaderboard').addEventListener('click', async () => {
	leaderboardContent.innerHTML = '<div class="lb-empty">Loading…</div>';
	leaderboardOverlay.classList.remove('hidden');
	try {
		const r = await fetch('/leaderboard?limit=10');
		const data = await r.json();
		renderLeaderboard(data);
	} catch (e) {
		leaderboardContent.innerHTML = '<div class="lb-empty">Could not load leaderboard</div>';
	}
});

document.getElementById('btn-leaderboard-close').addEventListener('click', () => {
	leaderboardOverlay.classList.add('hidden');
});

document.getElementById('btn-show-join').addEventListener('click', () => {
	if (!requireLandingName()) return;
	landingOverlay.classList.add('hidden');
	joinOverlay.classList.remove('hidden');
	joinError.textContent = '';
	joinInput.value = '';
	setTimeout(() => joinInput.focus(), 50);
});

document.getElementById('btn-join-cancel').addEventListener('click', () => {
	joinOverlay.classList.add('hidden');
	landingOverlay.classList.remove('hidden');
});

document.getElementById('btn-join-confirm').addEventListener('click', async () => {
	const rid = (joinInput.value || '').toLowerCase().trim();
	if (rid.length !== 4 || /[^a-z0-9]/.test(rid) || /[01oli]/.test(rid)) {
		joinError.textContent = 'Room code must be 4 chars (a-z 2-9, no 0/o/1/l/i)';
		return;
	}
	const joinBody = { room_id: rid, name: state.playerName };
	if (state.authToken) joinBody.auth_token = state.authToken;
	const r = await fetch('/join_room', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify(joinBody),
	});
	const data = await r.json();
	if (!r.ok) {
		if (r.status === 403 && (data.error || '').toLowerCase().includes('full')) {
			joinOverlay.classList.add('hidden');
			showLanding();
			showFullRoomPopup(`Room ${rid} is full.`);
			return;
		}
		joinError.textContent = data.error || 'Could not join room';
		announce(joinError.textContent);
		return;
	}
	saveCreds(data.room_id, data.player_token);
	history.replaceState(null, '', '/' + data.room_id);
	hideAllRoomOverlays();
	await loadState();
	startRealtime();
});

joinInput.addEventListener('keydown', e => {
	if (e.key === 'Enter') document.getElementById('btn-join-confirm').click();
});

function openNameEditor() {
	if (!state.roomId || state.mySlot === null) return;
	nameInput.value = state.names[state.mySlot] || state.playerName || '';
	nameError.textContent = '';
	nameOverlay.classList.remove('hidden');
	setTimeout(() => nameInput.focus(), 50);
}

document.getElementById('score-bar').addEventListener('click', (e) => {
	if (state.mySlot !== null && e.target.id === `name-label-${state.mySlot}`) openNameEditor();
});

document.getElementById('btn-name-cancel').addEventListener('click', () => {
	nameOverlay.classList.add('hidden');
});

document.getElementById('btn-name-save').addEventListener('click', async () => {
	const name = cleanName(nameInput.value);
	if (!name) {
		nameError.textContent = 'Letters only, 1-9 chars';
		return;
	}
	const r = await fetch('/set_name', {
		method: 'POST',
		headers: { 'Content-Type': 'application/json' },
		body: JSON.stringify({ ...authParams(), name }),
	});
	const data = await r.json();
	if (!r.ok) {
		nameError.textContent = data.error || 'Could not save';
		announce(nameError.textContent);
		return;
	}
	saveName(name);
	nameOverlay.classList.add('hidden');
	await loadState(true);
});

nameInput.addEventListener('keydown', e => {
	if (e.key === 'Enter') document.getElementById('btn-name-save').click();
});

landingNameInput.addEventListener('input', () => {
	landingNameInput.value = cleanName(landingNameInput.value);
});

nameInput.addEventListener('input', () => {
	nameInput.value = cleanName(nameInput.value);
});

const bagOverlay = document.getElementById('bag-overlay');
const bagGrid = document.getElementById('bag-grid');
const bagTotal = document.getElementById('bag-total');
const ALPHA = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

function openBagInspector() {
	const counts = state.unseenLetters || {};
	const items = [];
	for (const letter of ALPHA) {
		const n = counts[letter] || 0;
		items.push({ letter, n });
	}
	const blanks = counts['_'] || 0;
	items.push({ letter: '?', n: blanks, isBlank: true });
	bagGrid.innerHTML = items.map(it => `
		<div class="bag-cell ${it.n === 0 ? 'empty' : ''} ${it.isBlank ? 'blank' : ''}">
			<span class="bag-letter">${it.letter}</span>
			<span class="bag-num">${it.n}</span>
		</div>
	`).join('');
	const total = Object.values(counts).reduce((a, b) => a + b, 0);
	bagTotal.textContent = `${total} unseen tile${total === 1 ? '' : 's'} (${state.isSpectator ? 'bag' : "bag + other players' racks"})`;
	bagOverlay.classList.remove('hidden');
}

const btnMenuBag = document.getElementById('btn-menu-bag');
if (btnMenuBag) {
	btnMenuBag.addEventListener('click', () => {
		menuOverlay.classList.add('hidden');
		openBagInspector();
	});
}
bagOverlay.addEventListener('click', (e) => {
	if (e.target === bagOverlay) bagOverlay.classList.add('hidden');
});

const menuOverlay = document.getElementById('menu-overlay');
const btnMenu = document.getElementById('btn-menu');
if (btnMenu && menuOverlay) {
	btnMenu.addEventListener('click', () => {
		const chal = document.getElementById('btn-menu-challenge');
		if (chal) {
			if (state.challengeMode) {
				chal.classList.remove('hidden');
				chal.classList.toggle('disabled-soft', !state.canChallenge);
				chal.title = state.canChallenge ? 'Challenge the last play' : 'Nothing to challenge';
			} else {
				chal.classList.add('hidden');
			}
		}
		menuOverlay.classList.remove('hidden');
	});
	menuOverlay.addEventListener('click', (e) => {
		if (e.target === menuOverlay) menuOverlay.classList.add('hidden');
	});
}

let pollTimer = null;
let eventSource = null;
let sseConnected = false;
let pendingRefresh = false;

async function safeRefresh() {
	if (drag) {
		pendingRefresh = true;
		return;
	}
	pendingRefresh = false;
	await loadState(true);
}

let lastSseAt = 0;
function markSse() { lastSseAt = Date.now(); sseConnected = true; }

function startPolling() {
	stopRealtime();
	if (state.roomId && state.playerToken && typeof EventSource !== 'undefined') {
		try {
			eventSource = new EventSource(`/events?${authQuery()}`);
			eventSource.addEventListener('state', () => { markSse(); safeRefresh(); });
			eventSource.addEventListener('connected', () => { markSse(); });
			eventSource.onerror = () => { sseConnected = false; };
		} catch (e) {
			eventSource = null;
		}
	}
	pollTimer = setInterval(async () => {
		if (pendingRefresh) { await safeRefresh(); return; }
		const stale = lastSseAt && (Date.now() - lastSseAt > 20000);
		if (sseConnected && !stale && !state.vsComputer) return;
		await safeRefresh();
	}, 1000);
}

function stopRealtime() {
	if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
	if (eventSource) { try { eventSource.close(); } catch (e) {} eventSource = null; }
	sseConnected = false;
	lastSseAt = 0;
}

const startRealtime = startPolling;

async function init() {
	buildBoard();
	syncAppPadding();
	migrateRoomsMap();
	const urlRid = getUrlRoomId();
	const watchMode = isUrlWatchMode();
	const watchSuffix = watchMode ? '?watch=true' : '';
	// If URL points to a room we already have creds for, activate that room instead of clearing.
	if (urlRid) {
		const cred = getRoomCred(urlRid);
		if (cred) {
			state.roomId = urlRid;
			state.playerToken = cred.token;
			localStorage.setItem('scrabble_room_id', urlRid);
			localStorage.setItem('scrabble_player_token', cred.token);
		} else if (state.roomId && state.roomId !== urlRid) {
			// No cred for the URL's room — leave our current active room in the rooms map (not cleared).
			state.roomId = null;
			state.playerToken = null;
			localStorage.removeItem('scrabble_room_id');
			localStorage.removeItem('scrabble_player_token');
		}
	} else {
		// URL is "/" — don't auto-enter a game just because another tab is in one.
		// Show the landing page (with the active-games list) instead.
		state.roomId = null;
		state.playerToken = null;
		localStorage.removeItem('scrabble_room_id');
		localStorage.removeItem('scrabble_player_token');
	}
	if (state.roomId && state.playerToken) {
		loadChatSeen();
		const ok = await loadState();
		if (ok) {
			hideAllRoomOverlays();
			startRealtime();
			if (urlRid !== state.roomId) history.replaceState(null, '', '/' + state.roomId + watchSuffix);
			return;
		} else {
			// /state failed — room probably gone. Drop the cred.
			if (state.roomId) delRoomCred(state.roomId);
			state.roomId = null;
			state.playerToken = null;
			localStorage.removeItem('scrabble_room_id');
			localStorage.removeItem('scrabble_player_token');
		}
	}
	let fullMessage = '';
	if (urlRid) {
		try {
			const checkR = await fetch(`/room_exists/${urlRid}`);
			const info = await checkR.json();
			if (!info.exists) {
				fullMessage = `Room ${urlRid} no longer exists.`;
				history.replaceState(null, '', '/');
			} else if (info.full && !watchMode) {
				fullMessage = `Room ${urlRid} is full (${info.player_count || 'max'} players already).`;
				history.replaceState(null, '', '/');
			} else if (window.location.pathname + window.location.search !== '/' + urlRid + watchSuffix) {
				history.replaceState(null, '', '/' + urlRid + watchSuffix);
			}
		} catch (e) {
			history.replaceState(null, '', '/');
		}
	}
	showLanding();
	if (fullMessage) showFullRoomPopup(fullMessage);
}

function showFullRoomPopup(text) {
	const el = document.getElementById('full-room-popup');
	if (!el) return;
	el.textContent = text;
	el.classList.remove('hidden');
	setTimeout(() => el.classList.add('hidden'), 4000);
}

(function disableAllZoom() {
	let lastTouchEnd = 0;
	document.addEventListener('touchend', (e) => {
		const now = Date.now();
		if (now - lastTouchEnd <= 350) {
			e.preventDefault();
		}
		lastTouchEnd = now;
	}, { passive: false, capture: true });

	document.addEventListener('touchstart', (e) => {
		if (e.touches.length > 1) e.preventDefault();
	}, { passive: false, capture: true });

	document.addEventListener('gesturestart', (e) => e.preventDefault());
	document.addEventListener('gesturechange', (e) => e.preventDefault());
	document.addEventListener('gestureend', (e) => e.preventDefault());

	document.addEventListener('dblclick', (e) => e.preventDefault());

	document.addEventListener('wheel', (e) => {
		if (e.ctrlKey || e.metaKey) e.preventDefault();
	}, { passive: false });

	document.addEventListener('keydown', (e) => {
		if ((e.ctrlKey || e.metaKey) && ['=', '+', '-', '_', '0'].includes(e.key)) {
			e.preventDefault();
		}
	});
})();

init();
