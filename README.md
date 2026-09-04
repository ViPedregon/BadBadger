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
- append-only belief evidence with source provenance and deterministic
  contradiction resolution;
- bounded NPC decision events that can produce independently validated actions;
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
