# BadBadger
Initial attempt at a dialogue based game

## Version 0.1 deterministic slice

The new engine foundation stores authoritative state in SQLite. LLM-facing
components will be added above this layer; they will propose structured actions
but will not write canonical state directly.

The current slice includes:

- persistent locations, characters, facts, and character-scoped beliefs;
- deterministic movement, examination, and waiting;
- integer mission time and scheduled event processing;
- an append-only action/event history; and
- a disposable two-room prototype created by
  `badbadger.application.create_prototype`.

Run its dependency-free tests from the repository root:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -p "test_vertical_slice.py" -v
```

The older CLI remains available through the `badbadger` command while it is
gradually migrated to the SQLite engine.
