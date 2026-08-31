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
