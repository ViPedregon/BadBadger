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
Sable, Orr or Lin with this cast. The opening beta chapter,
[Blackbarrow: The North Road](BLACKBARROW.md), now uses these identities and their
established friendship in its own scenario.

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
# Player-order decisions

An NPC action can define an optional `effects.order_response` policy with
`accept_score`, `refuse_below`, `condition_message` and `refusal_message`.
The engine evaluates the action's existing `decision` factors against current
character state. Scores at or above acceptance execute the order. Intermediate
scores produce the authored condition, and lower scores produce the authored
refusal. A condition leaves the action available for a later request after the
relevant state changes. Location, prerequisites and trust gates still apply.

Public replies use only authored messages. Full scores and decision reasons are
recorded in the private dialogue action history, where biography influences can
be inspected without disclosing them to the player.

Blackbarrow's route and guard orders use goals, beliefs, trust and authored
personality parameters. Their weights are initial design choices for playtesting.
Existing saves retain their scenario policies. Start a new Blackbarrow game to
exercise these new policies. Actions without an order-response policy retain
their previous acceptance rules.

## Weighted motivations

Chapter parameters can define `motivation_weights`, mapping stable motivation
ids to strengths from 0 to 1. A decision's `motivation_factors` lists
`motivation_id`, signed `impact`, and a private diagnostic `reason`. The score
adds strength times impact, so a motivation can favor one plan and oppose
another. Unknown motivation references, invalid strengths, and non-finite
impacts are rejected before scenario creation. Runtime parameter updates also
validate strengths. These weights are explicit mechanical interpretations of
the dossiers, not automatically inferred from their prose or new biography.

Matilda weighs reliable field intelligence and independent judgment. Horace
weighs convoy protection against preserving household readiness: a crossing or
grain guard spends guards, while holding the convoy preserves readiness at the
cost of supplies and delay. All four North Road actions now use character
decisions for player orders and autonomous choices. Their autonomous minimum
equals their order acceptance threshold, preventing an independent action from
bypassing a condition the character would place on the same order.

Weights persist with NPC parameters in saves and backups. They are authored
defaults; conversation does not rewrite them. Public responses remain authored
and do not disclose scores or private reasons. All initial plans remain viable;
the weights and thresholds need playtesting. Start a new Blackbarrow game to
load these expanded policies; existing games keep their scenario snapshot.

## Player character section and portraits

The browser's Characters section lets the player inspect each member of the
cast without submitting a turn. Cards show role, personality, strengths,
limitations, motivations, values, appearance, and public conscious biography.
They use saved identity snapshots rather than live NPC parameters. Hidden goals,
beliefs, private biography, author notes, and decision scores are not included.

Portraits are optional presentation assets in `src/badbadger/static/portraits`.
Use `victor_gravenholt.png`, `matilda_ash.png`, and `horace.png` (WebP and JPG
also work). A 3:4 crop fits the card. Initials appear until artwork is supplied;
adding an image requires no new game. Portrait files are packaged with the app
and served behind its existing authentication.

Character cards also show whether a companion is here, elsewhere, travelling,
or unavailable. They do not reveal offscreen locations or travel destinations.
When conversation is possible, cards offer a priority question and discussions
of currently available public plans. After a decision they offer an outcome
question instead; finished chapters retain inspection without conversation
buttons. Buttons fill the command input, and Send starts the one-minute
conversation. The server rechecks presence and prerequisites when it executes
the command. No new game is required for these card controls, although older
scenario snapshots may not contain plan-discussion topics.

## Discussing plans

North Road suggestions now offer a Discuss command beside each currently
available public plan. For example, `ask Horace about controlled crossing` or
`Matilda, what concerns you about the high track?` returns authored benefits,
costs, and the companion's current willingness. Trust and willingness are
rechecked for every discussion. A condition or refusal uses the same public
response as an order; private score reasons stay in diagnostics.

This response works without a model and never proposes actions or changes
beliefs, motivation weights, or trust. Conversation still spends one mission
minute, so scheduled events and independent companion behavior continue as
normal. Clicking a Discuss suggestion only fills the input; Send starts the
conversation. A separate explicit request is required to commit the plan.
Unavailable plans receive a short availability reply. Existing saves retain
their scenario snapshots, so start a new Blackbarrow game to load plan advice.

Authors define `effects.plan_advice` with non-empty `topic`, `benefit`, `cost`,
and `acceptance_message` strings. Advice is exposed only for actions listed in
the scenario's `public_action_ids`; it does not publish arbitrary action data.
