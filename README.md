# BadBadger
Initial attempt at a dialogue based game

## Version 0.1 deterministic slice

The new engine foundation stores authoritative state in SQLite. LLM-facing
components will be added above this layer; they will propose structured actions
but will not write canonical state directly.

The current slice includes:

- persistent locations, characters, facts, and character-scoped beliefs;
- deterministic movement, examination, and waiting;
- persistent NPC dialogue with character-scoped context and beliefs;
- persistent, character-scoped episodic memories from witnessed events;
- persistent NPC goals with stable ids, priorities, lifecycle status, and
  append-only resolution history;
- directional NPC trust and affinity with an append-only change history;
- relationship-driven action scoring and consequence-driven attitude changes;
- trust-gated private disclosures and requested NPC cooperation;
- autonomous, engine-validated NPC-to-NPC belief sharing;
- explainable belief-, personality-, and memory-driven scoring for autonomous
  consequential actions;
- append-only belief evidence with source provenance and deterministic
  contradiction resolution;
- bounded NPC decision events that can produce independently validated actions;
- scenario-generic NPC action affordances derived from the current world graph;
- JSON scenario loading with persistent, inspectable NPC parameters;
- engine-validated, scenario-defined NPC interactions with consequential effects;
- presentation-only appearance profiles for consistent future scene imagery;
- integer mission time and scheduled event processing;
- an append-only action/event history; and
- a disposable two-room prototype created by
  `badbadger.application.create_prototype`.

Run its dependency-free tests from the repository root:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -p "test_vertical_slice.py" -v
```

Run the playable SQLite-backed CLI with:

```powershell
$env:PYTHONPATH = "src"
python -m badbadger.cli --save my-game.db
```

Type `npc` to list NPCs or `npc Observer` to inspect an NPC's location,
activity, parameters, goals, and beliefs. The inspector deliberately excludes
hidden objective facts.

To start a new save from a custom scenario, copy
`src/badbadger/scenarios/prototype.json`, edit it, and run:

```powershell
python -m badbadger.cli --save custom-game.db --scenario path\to\scenario.json
```

The scenario file defines locations, connections, characters, starting beliefs,
goals, facts, and scheduled events. NPC `parameters` are free-form JSON values;
the engine currently acts on `autonomy_enabled`, `decision_cooldown_minutes`,
and `first_decision_after_minutes`, while passing all parameters into the NPC's
filtered LLM context.

Autonomous decisions also receive only engine-computed `available_actions`.
For movement, these contain directly connected destinations and authoritative
travel durations. The backend selects or declines an affordance; the engine
still validates the proposal and owns all resulting state changes.

## Playable proof scenario: Relay Crisis

`relay_crisis.json` is a short scenario with three NPCs whose goals conflict
and whose beliefs contain different pieces of the situation. Each has one
irreversible option, and the first completed option disables the others. Start
a fresh save with:

```powershell
$env:PYTHONPATH = "src"
python -m badbadger.cli --save relay-crisis.db --scenario src\badbadger\scenarios\relay_crisis.json
```

Begin with `examine situation table`, use `npc` and `npc <name>` for debugging,
and speak naturally to Director Sable, Technician Orr, and Dr. Lin. A compact
offline path is `go to Relay Chamber`, followed by
`tell Technician Orr broadcast the distress packet`. With the OpenAI backend,
ask what each character knows before deciding whom to inform or encourage.
The opening decision schedule leaves enough time to question multiple NPCs;
ordinary information requests cannot authorize an irreversible interaction.
After a decisive action, the simulation remains active for a ten-minute
aftermath. Ask affected characters `what now?` or `how do you feel?` to hear
reactions grounded in their resolved goals, or inspect them directly. A
validated scheduled event ends the chapter after that window.

Scenario `appearance` objects establish repeatable character and location
details for future image prompts. They are descriptive inputs, not hidden truth;
future renderers should receive only the current visible state and must never
write facts back to the simulation. Rendering should run asynchronously so it
cannot pause the game clock or action loop.

Launching it again with the same `--save` path resumes the simulation. The
current deterministic text interpreter accepts natural-looking forms of the
small Version 0.1 action vocabulary; an LLM interpreter will later replace it
without gaining direct database access.

Try `ask the Observer whether Room B is safe` or
`tell Observer the lights in Room B are out` while in Room A. NPC context is
built from that character's location, beliefs, and recent dialogue; objective
hidden facts are never included.

Beliefs retain evidence labeled as `initial`, `direct`, `hearsay`, `inference`,
or `legacy`. New evidence never blindly overwrites an existing belief. Evidence
scores select the current value, recency breaks score ties, and contradictory
evidence lowers the resolved confidence. Dialogue-derived updates are recorded
as hearsay from the player.

Scenario interactions may declare concise `observations`. When an interaction
resolves, the deterministic engine stores that observation only for active NPCs
at the interaction's location. NPC context retrieves at most eight memories,
ordered by salience and then recency. Observation text is explicitly authored
as visible information; hidden fact payloads are never converted into memories
or exposed automatically. Use `npc <name>` to inspect the resulting memory view.

Relationships are private to the observing NPC: Sable's trust in Orr is
separate from Orr's trust in Sable. Scenario files can define starting `trust`
and `affinity` values from -1 to 1. An NPC backend may propose small changes
during dialogue, but the engine accepts at most ±0.25 per exchange, requires a
reason, and rejects targets the NPC cannot currently see. Relationship state
and its change history persist in SQLite and are available through `npc <name>`.
Decision policies may also score actions from a specific directional trust or
affinity threshold. Validated action effects can change relationships as world
consequences, with separate action provenance; effects marked
`when_requested_by_player` apply only when the player actually requested that
choice, so autonomous NPC decisions do not silently blame or credit the player.

Scenario-defined disclosures keep their actual content in authoritative engine
storage. NPC backends receive only a topic, request phrases, and current
eligibility; they may request a disclosure by id but cannot read the secret.
The engine checks the NPC's pre-exchange trust, reveals authorized content,
and records the information as a sourced player belief. Scenario interactions
can separately require player trust when requested in dialogue, while the same
NPC remains free to choose that action autonomously. The NPC inspector reports
each topic as `withheld`, `available`, or `shared` without printing its content.

On autonomous decision ticks, co-located NPCs may also select bounded
`share_belief` affordances. The engine rechecks that the speaker actually owns
the belief and that the listener is present, records hearsay evidence and
episodic memories, and adjusts the listener's relationship toward the speaker.
Protected disclosure content is never included in these affordances. Offscreen
exchanges produce no player narration or knowledge; a player in the room hears
the exchange and gains corresponding hearsay evidence.

NPCs can also react aloud to engine-authored cues such as a character arriving,
a goal resolving, or a substantial relationship change. These cues contain
only the visible summary selected by the deterministic engine, expire if they
are no longer timely, and become a `speak` affordance only while the player and
NPC share a location. The engine treats the model's dialogue as audible only
when it accompanies that exact cue id, then persists the utterance as dialogue
and a memory for everyone present. Reaction-only turns cannot move, interact,
or change relationships, and `proactive_speech_cooldown_minutes` (default 3)
keeps repeated reactions from becoming chatter. A reaction does not postpone
the NPC's regular autonomy schedule.

Scenario actions may define a base decision score, a minimum autonomous score,
and modifiers based on resolved beliefs, inspectable NPC parameters, or scoped
episodic memories. Parameter factors support exact matches and numeric `gte` or
`lte` comparisons, allowing traits such as risk tolerance to have deterministic
gameplay effects. Memory factors match authored event kind, subject, source,
salience, and optional recency without searching prose or reading hidden facts.
Goal factors multiply an authored score by the matching goal's priority and can
require a lifecycle status. Validated action effects may complete, fail, or
abandon goals for any NPC, retaining the reason and source action. The engine
includes human-readable reasons in filtered context and decision history, then
rechecks eligibility before applying an autonomous action. Player requests
remain a separate path with their own trust gates. Use `npc <name>` to inspect
the current score, goal outcomes, and the state affecting them.

Hearsay confidence is calibrated by the listener's directional trust in the
speaker before it becomes evidence. Neutral trust preserves 62.5% of claimed
confidence, complete distrust preserves 25%, and complete trust preserves
100%. This applies to player-to-NPC claims and autonomous NPC information
sharing, and the calibration is retained in action or dialogue history.

## Optional OpenAI NPC dialogue

The default NPC backend remains deterministic and requires no network access.
To enable structured LLM dialogue through the OpenAI Responses API:

```powershell
python -m pip install -e ".[openai]"
$env:OPENAI_API_KEY = "your-api-key"
$env:BADBADGER_OPENAI_MODEL = "gpt-5.6"  # optional override
python -m badbadger.cli --save my-game.db
```

The client uses `responses.parse` with a strict Pydantic response model and
`store=False`. It retries one malformed response, then uses the deterministic
backend if the API call still fails. The API receives only the filtered NPC
context and exact player input; it cannot access SQLite or directly execute
proposed actions.

When OpenAI mode is enabled, unfamiliar player phrasing also goes through a
separate structured intent interpreter. Familiar commands are still parsed
locally to avoid unnecessary API calls. The intent model can propose movement,
examination, waiting, conversation, or `unknown`; the deterministic engine
validates every target and duration before changing state.
