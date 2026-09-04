PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS simulation (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    scenario_id TEXT NOT NULL,
    current_time_minutes INTEGER NOT NULL DEFAULT 0 CHECK (current_time_minutes >= 0),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'succeeded', 'failed', 'ended'))
);

CREATE TABLE IF NOT EXISTS locations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS location_connections (
    origin_id TEXT NOT NULL REFERENCES locations(id), destination_id TEXT NOT NULL REFERENCES locations(id),
    duration_minutes INTEGER NOT NULL CHECK(duration_minutes > 0), PRIMARY KEY(origin_id,destination_id)
);

CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('player', 'npc')),
    name TEXT NOT NULL,
    location_id TEXT NOT NULL REFERENCES locations(id),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    state_json TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS one_player
ON characters(kind) WHERE kind = 'player';

CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_json TEXT NOT NULL,
    hidden INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1)),
    UNIQUE(subject_id, predicate)
);

CREATE TABLE IF NOT EXISTS beliefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id TEXT NOT NULL REFERENCES characters(id),
    subject_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_json TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0 CHECK (confidence >= 0 AND confidence <= 1),
    source_event_id INTEGER,
    updated_at_game_time INTEGER NOT NULL,
    UNIQUE(character_id, subject_id, predicate)
);

CREATE TABLE IF NOT EXISTS belief_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    belief_id INTEGER NOT NULL REFERENCES beliefs(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL
        CHECK (source_type IN ('initial', 'direct', 'hearsay', 'inference', 'legacy')),
    source_character_id TEXT REFERENCES characters(id),
    value_json TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    detail TEXT,
    game_time INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS evidence_by_belief
ON belief_evidence(belief_id, id);

CREATE TABLE IF NOT EXISTS scheduled_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    due_time INTEGER NOT NULL CHECK (due_time >= 0),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processed', 'cancelled')),
    payload_json TEXT NOT NULL DEFAULT '{}',
    cancellation_key TEXT
);
CREATE TABLE IF NOT EXISTS character_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT, character_id TEXT NOT NULL REFERENCES characters(id),
    kind TEXT NOT NULL, origin_id TEXT NOT NULL REFERENCES locations(id), destination_id TEXT REFERENCES locations(id),
    started_at INTEGER NOT NULL, due_time INTEGER NOT NULL, event_id INTEGER, status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','completed','cancelled'))
);

CREATE TABLE IF NOT EXISTS npc_goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id TEXT NOT NULL REFERENCES characters(id),
    description TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    UNIQUE(character_id, description)
);

CREATE INDEX IF NOT EXISTS due_events
ON scheduled_events(status, due_time, id);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_time INTEGER NOT NULL,
    record_type TEXT NOT NULL,
    actor_id TEXT,
    input_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS dialogue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_time INTEGER NOT NULL,
    npc_id TEXT NOT NULL REFERENCES characters(id),
    speaker_id TEXT NOT NULL REFERENCES characters(id),
    text TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS dialogue_by_npc
ON dialogue(npc_id, id);
