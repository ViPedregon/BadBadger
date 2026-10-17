PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS simulation (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    scenario_id TEXT NOT NULL,
    current_time_minutes INTEGER NOT NULL DEFAULT 0 CHECK (current_time_minutes >= 0),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'succeeded', 'failed', 'ended'))
);

CREATE TABLE IF NOT EXISTS scenario_presentation (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    details_json TEXT NOT NULL DEFAULT '{}'
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

CREATE TABLE IF NOT EXISTS character_canon (
    character_id TEXT PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT '',
    voice TEXT NOT NULL DEFAULT '',
    values_json TEXT NOT NULL DEFAULT '[]',
    boundaries_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS character_biography_fragments (
    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    fragment_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    behavioral_guidance TEXT,
    action_influences_json TEXT NOT NULL DEFAULT '[]',
    importance REAL NOT NULL DEFAULT 0.5
        CHECK(importance >= 0 AND importance <= 1),
    visibility TEXT NOT NULL DEFAULT 'private'
        CHECK(visibility IN ('public','private','secret')),
    awareness TEXT NOT NULL DEFAULT 'conscious'
        CHECK(awareness IN ('conscious','suppressed','forgotten')),
    certainty REAL NOT NULL DEFAULT 1.0
        CHECK(certainty >= 0 AND certainty <= 1),
    topics_json TEXT NOT NULL DEFAULT '[]',
    emotional_tags_json TEXT NOT NULL DEFAULT '[]',
    related_character_ids_json TEXT NOT NULL DEFAULT '[]',
    period TEXT,
    source TEXT,
    PRIMARY KEY(character_id, fragment_id)
);

CREATE INDEX IF NOT EXISTS biography_fragments_by_character
ON character_biography_fragments(character_id, importance DESC, fragment_id);

CREATE TABLE IF NOT EXISTS npc_parameters (
    character_id TEXT PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
    parameters_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS appearances (
    subject_id TEXT NOT NULL,
    subject_kind TEXT NOT NULL CHECK(subject_kind IN ('character', 'location')),
    details_json TEXT NOT NULL,
    PRIMARY KEY(subject_kind, subject_id)
);

CREATE TABLE IF NOT EXISTS npc_actions (
    id TEXT PRIMARY KEY,
    character_id TEXT NOT NULL REFERENCES characters(id),
    location_id TEXT NOT NULL REFERENCES locations(id),
    label TEXT NOT NULL,
    description TEXT NOT NULL,
    preconditions_json TEXT NOT NULL DEFAULT '[]',
    effects_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'available'
        CHECK(status IN ('available', 'used'))
);

CREATE TABLE IF NOT EXISTS npc_disclosures (
    id TEXT PRIMARY KEY,
    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    topic TEXT NOT NULL,
    request_terms_json TEXT NOT NULL,
    min_trust REAL NOT NULL CHECK(min_trust >= -1 AND min_trust <= 1),
    content TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    predicate TEXT NOT NULL,
    value_json TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1)
);

CREATE TABLE IF NOT EXISTS disclosure_events (
    disclosure_id TEXT NOT NULL REFERENCES npc_disclosures(id) ON DELETE CASCADE,
    recipient_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    game_time INTEGER NOT NULL,
    PRIMARY KEY(disclosure_id, recipient_id)
);

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
    goal_id TEXT,
    description TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active','completed','failed','abandoned')),
    UNIQUE(character_id, description)
);

CREATE TABLE IF NOT EXISTS npc_goal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_row_id INTEGER NOT NULL REFERENCES npc_goals(id) ON DELETE CASCADE,
    game_time INTEGER NOT NULL,
    status TEXT NOT NULL
        CHECK(status IN ('active','completed','failed','abandoned')),
    reason TEXT NOT NULL,
    source_action_id TEXT
);

CREATE INDEX IF NOT EXISTS goal_events_by_goal
ON npc_goal_events(goal_row_id, id);

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

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    game_time INTEGER NOT NULL,
    location_id TEXT NOT NULL REFERENCES locations(id),
    memory_kind TEXT NOT NULL,
    subject_id TEXT,
    summary TEXT NOT NULL,
    salience REAL NOT NULL DEFAULT 0.5 CHECK(salience >= 0 AND salience <= 1),
    source_character_id TEXT REFERENCES characters(id)
);

CREATE TABLE IF NOT EXISTS relationships (
    observer_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    trust REAL NOT NULL DEFAULT 0 CHECK(trust >= -1 AND trust <= 1),
    affinity REAL NOT NULL DEFAULT 0 CHECK(affinity >= -1 AND affinity <= 1),
    updated_at_game_time INTEGER NOT NULL,
    PRIMARY KEY(observer_id, target_id),
    CHECK(observer_id <> target_id)
);

CREATE TABLE IF NOT EXISTS relationship_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observer_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    game_time INTEGER NOT NULL,
    source_type TEXT NOT NULL CHECK(source_type IN ('initial', 'dialogue')),
    trust_delta REAL NOT NULL,
    affinity_delta REAL NOT NULL,
    reason TEXT NOT NULL,
    FOREIGN KEY(observer_id, target_id)
        REFERENCES relationships(observer_id, target_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS relationship_events_by_pair
ON relationship_events(observer_id, target_id, id DESC);

CREATE TABLE IF NOT EXISTS relationship_action_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observer_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    game_time INTEGER NOT NULL,
    trust_delta REAL NOT NULL,
    affinity_delta REAL NOT NULL,
    reason TEXT NOT NULL,
    source_action_id TEXT NOT NULL,
    FOREIGN KEY(observer_id, target_id)
        REFERENCES relationships(observer_id, target_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS relationship_action_events_by_pair
ON relationship_action_events(observer_id, target_id, id DESC);

CREATE INDEX IF NOT EXISTS memories_by_character
ON memories(character_id, salience DESC, game_time DESC, id DESC);

CREATE TABLE IF NOT EXISTS npc_reaction_cues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    created_time INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    trigger_kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    salience REAL NOT NULL DEFAULT 0.5 CHECK(salience >= 0 AND salience <= 1),
    source_character_id TEXT REFERENCES characters(id),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','spoken','expired')),
    spoken_at INTEGER,
    utterance TEXT
);

CREATE INDEX IF NOT EXISTS reaction_cues_by_npc
ON npc_reaction_cues(npc_id, status, salience DESC, created_time DESC, id DESC);

CREATE INDEX IF NOT EXISTS dialogue_by_npc
ON dialogue(npc_id, id);
