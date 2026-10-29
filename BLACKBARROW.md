# Blackbarrow: The North Road — first playable beta chapter

This is new game content built on the supplied House Gravenholt continuity.
The convoy dispute, rain-damaged causeway and branching resolutions are original
beta chapter material, not facts retroactively attributed to the user's lore cards.
The established trio, voices, authority limits, equipment and familiar bond remain
as supplied. The Valenbruck succession story, Elena and later romantic development
are not advanced by this chapter.

## Player experience

Victor is asked to settle the immediate problem of a convoy waiting on the north
road. Grain, medical supplies and families have competing needs as daylight fades.
Matilda and Horace already trust him; there is no stranger-introduction or repeated
gratitude requirement. They accompany him when free to travel and offer different
practical judgments. The opening's quiet private exchange establishes their
familiarity without making private nicknames a public introduction.

Three locations support a short investigation: the Gatehouse, Causeway and High
Track. The petition explains what is at stake; the ruts and ridge markers provide
field evidence. Inspections record direct knowledge in the player's journal and
the memories of companions actually present. They cannot be performed remotely.
Talking to the experts is also a viable way to decide: inspections inform judgment
and do not act as mandatory puzzle unlocks.

Plans are committed at the Gatehouse, where the waiting convoy can receive clear
instructions. Explicit requests remain required, and questions about a plan do not
authorize it. There are three branches:

| Plan | Immediate benefit | Cost carried in the save |
|---|---|---|
| Horace oversees a controlled crossing | Families, grain and medicine get through | Household guards are committed and the last good light is used |
| Matilda directs the high-track passage | People and portable medicine get to shelter without using the damaged wagon road | Grain wagons remain behind and need another arrangement |
| Horace holds the convoy | Shelter without a late crossing | Household stores are spent and deliveries are delayed |

The action selects one resolution and disables the others. The player can then
question the companions in a six-mission-minute aftermath, review the public recap,
and finish the chapter. Outcomes and their costs persist as engine facts and
witnessed memories; a generated sentence does not determine them. There is no
single declared correct solution or invented player dialogue.

## Time and independent companions

Travel takes two or three mission minutes, inspection two, and successful
conversation one. Reading menus/journal/cards is free. Waiting is capped at six
minutes per command so large time jumps do not bypass opportunities to respond.
Public warning beats occur as daylight fades. If Victor leaves matters unresolved,
the companions may choose a practical plan rather than wait forever.

For authoring/testing, Matilda's first regular decision is scheduled at minute 24
and Horace's at 28. These are decision opportunities, not guaranteed wall-clock
deadlines: location, occupation, validated proposals and backend availability still
matter. An autonomous resolution is never recorded as a player request. Baseline
trust is not proof that the player caused an outcome.

Initial directed trust of 0.8 and affinity of 0.65 are **chapter tuning** expressing
the supplied high mutual trust, not new canonical numerical personality claims.
The optional `follow_player` parameter belongs to mission state, not an identity
profile. A busy/departed companion is not teleported or available for conversation
at their old location. Ordinary shared arrivals do not produce redundant surprise
recaps between companions already travelling together.

The first chapter deliberately stays small: one problem, three plans, no new combat
system, no extra major named NPCs and no fabricated secrets or childhood histories.
It is a playable beta slice, not a completed multi-chapter campaign. Progression
into a subsequent chapter has not been implemented; New game starts a fresh
playthrough and archives the previous one.

## Browser selection and existing saves

Fresh web-server saves default to this chapter. Existing Relay Crisis saves always
resume as Relay Crisis, even if the configured default changes. To start Blackbarrow
from an existing game, choose **Blackbarrow: The North Road** in the chapter selector
beside **New game**, then confirm. Switching chapters archives the previous engine
state/transcript range atomically. It does not reinterpret a science-fiction save
as Gravenholt history.

Both chapter keys are server-owned (`blackbarrow`, `relay_crisis`); a browser cannot
load arbitrary scenario paths. The page title, objective, suggestions and player
card follow the active chapter. Relay Crisis remains labelled as a prototype in
the selector. Duplicate resets, stale tabs and cross-chapter retries retain the
existing receipt/revision protections. A request id cannot be reused to select a
different chapter.

The historical Render path `/var/data/badbadger/relay.db` may now hold either active
chapter plus archives. Keep that path when updating an existing service; the
filename does not restrict its contents. `BADBADGER_SCENARIO=blackbarrow` or
`--scenario blackbarrow` selects only the chapter for a previously nonexistent save.
Backup/restore supports both chapter ids and preserves profiles, observations,
receipts and archived playthroughs.

## Verified checks

Automated tests play all three branches, inspect evidence, check travel and occupied
companions, verify existing trust, question-versus-command boundaries, autonomous
decisions without false credit, chapter switching, retry safety, and backup/restore.
The real-browser test starts in Relay Crisis, selects Blackbarrow, investigates,
closes/reopens the browser, restarts the server, resumes, and completes the chapter.
Private future continuity and dossier notes are checked against player-facing output.

Offline play is deterministic and complete. AI-mode plumbing remains covered with
simulated structured provider responses. These tests do not establish the quality
of every live-generated conversation, configure off-site backups, create tester
accounts or deploy the service. Operational beta preparation remains a later step.
