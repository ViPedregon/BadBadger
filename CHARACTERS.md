# Persistent character identity

The confirmed production cast is Victor Gravenholt (player), Matilda Ash and
Captain Horace (NPCs). Their supplied established continuity is now encoded in
revision-2 profiles under `src/badbadger/scenarios/characters/`. The authoring
source and shared continuity ledger are in [lore/README.md](lore/README.md) and
[lore/house-gravenholt.json](lore/house-gravenholt.json).

Public roles/history, distinct voices, qualitative personality, strengths,
limitations, appearance and private companionship are preserved. Provisional
ages stay provisional; Horace has no chosen surname, and Matilda's unspecified
family history/early training remain unfilled. No numeric personality or trust
scores have been invented. A chapter must deliberately initialize the established
high mutual trust rather than treating the trio as strangers.

The romance and later Valenbruck/Elena developments remain authoring continuity,
not current episodic memories. Private nicknames remain private familiarity;
chapter context must establish their appropriate audience before using them.
Relay Crisis retains its existing cast and scenario; New game does not replace
Sable, Orr or Lin with the future cast. A representative production chapter is
the next content milestone after the identities are populated.

## Identity versus current mission

An identity profile has schema version, stable `identity_id`, profile `revision`,
content status, name and kind. Its `canon` contains role, voice, values, boundaries
and authored biography. `personality` provides scalar baseline traits;
`motivations` describes lasting drives. `appearance` is public descriptive input.
`author_notes` is private authoring metadata and is not supplied to NPC models.

Do not put location, mutable beliefs, active goals, current trust/affinity or
autonomy schedules in that file. Those belong to the scenario and saved mission
state. Relationship history belongs in biography, while the current directional
relationship scores are initialized by each chapter and evolve during play.
Models still propose changes through the existing engine; a dossier adds no
ability to mutate canonical facts or invent actions.
Role, voice, personality and motivations are model-facing characterization, not
secret storage. Sensitive history belongs in private/secret biography fragments
with separately authored, spoiler-free behavioral guidance.

An NPC's context receives only its own stable canon, personality, motivations,
selected safe biography guidance, and current mission state. It does not receive
another character's full dossier. The player's canon is not supplied to NPCs as
permission to invent player feelings, thoughts, choices or words.

## Referencing a profile

An authored chapter can reference profiles inside its scenario directory tree:

```json
{
  "id": "victor",
  "identity_file": "characters/victor-gravenholt.json",
  "location_id": "chapter_start"
}
```

Name and kind come from the profile. The scene still supplies locations,
connections, goals, initial beliefs and initial relationships. NPC personality
defaults become engine parameters, alongside scenario scheduling parameters.
Scenario fields cannot silently rename the actor, change player/NPC kind,
overwrite stable personality or mix `identity_file` with `canon`/`canon_file`.
Scheduling values are not accepted inside identity personality.

Shared profile biographies use `related_identity_ids`. The loader maps identities
present in the scenario to their local character ids. An identity not present
is retained in the stored source snapshot but is omitted from local relevance
links; loading a dossier never spawns an absent character. This supports reusing
the same profile in chapters with different local actor ids. Scenario-specific
`canon_file` dossiers continue to support `related_character_ids` as before.

Mission-specific `action_influences` are not accepted inside reusable identity
profiles. Use chapter action decision policies and inherited personality traits
for those effects. Existing scenario-bound canon support remains available.

The loader validates profile schema, canon and biography; rejects unknown fields,
duplicate identities, inconsistent overrides and invalid trait values; and
rejects absolute paths, parent traversal and symlinks escaping the scenario root.
Validation completes before a new database is created.

## Authoring status and saved revisions

- `awaiting_lore`: confirmed identity only; content is still missing.
- `draft`: proposed or incomplete character material.
- `approved`: authored content intended for use in a chapter.

Ordinary scenario loading accepts only approved profiles. A development caller
can explicitly opt in through `load_scenario(..., allow_unapproved_identities=True)`
for a scratch authoring scene. The web API offers no such override or client
scenario-selection option.

Loading a new game stores a complete profile snapshot, its stable id, revision
and status in `character_identity`. Canon and mission state have separate tables.
Editing the JSON file does not silently rewrite a resumed game's name, voice,
history or personality. New saves use the new revision; updating an existing
save's identity would require a separately designed, explicit migration.
No migration of old Relay Crisis identities is performed by this feature.
Reusing a profile in another save does not automatically transfer episodic
memories or live relationships between chapters; that would require an explicit
chapter-transition feature rather than silently merging independent games.

The table is additive, so existing saves load without identity profiles. Backup
and new-game archiving include the identity snapshot with the engine state.
Profiles are packaged with the application but have no public download route.

## Player identity and agency

The time-free `self` command (also “my character” or “who am I”) shows the player's
name, role, values and public conscious biography. It excludes private, secret,
suppressed and forgotten entries and never shows an NPC dossier. The browser's
**Your character** shortcut fills that command without submitting it.
The player remains free to choose their words and actions; values are role
guidance, not forced behavior. Player-known private background can later be
introduced explicitly through chapter-authored knowledge rather than revealing
every hidden biography fragment automatically.

## What to supply as lore

Free-form notes are sufficient; the user does not need to write JSON. Useful
material includes each character's role, speaking style, values, fears, enduring
motivations, formative events, and history with the other two characters. Mark
what is public, private or secret, what the character consciously remembers,
and which recollections are uncertain or mistaken. Also distinguish what Victor
knows from facts known only to Matilda or Horace. Unspecified details stay blank.

Encoding supplied notes should preserve their meaning and mark unresolved
interpretations in author notes rather than turn guesses into canon. Personality
defaults and chapter-specific mechanical consequences should be distinguished
from biographical facts. No live AI service is required to validate the files.

## Automated coverage

Identity tests exercise reuse across chapters/local ids, save pinning after file
edits, stable identity alongside changed beliefs/relationships, owner-scoped NPC
context, public player cards, strict schema/path validation and rejection of
unprovided production lore. The existing conversation/privacy, browser,
new-game, backup/restore and Relay Crisis outcome checks remain in the suite.
Package checks verify that installed builds include the three profile files.
