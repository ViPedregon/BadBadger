# BadBadger playtest

The opening Gravenholt beta chapter is now **Blackbarrow: The North Road**;
its scope, branch costs and automated coverage are in [BLACKBARROW.md](BLACKBARROW.md).
The procedure below remains useful for the Relay Crisis prototype. Neither
chapter's automated coverage replaces eventual external playtesting, but users
do not need to manually recheck every implementation checkpoint.

## Blackbarrow companion loop

Start a new Blackbarrow game to load the current scenario snapshot. Inspect the
Characters section, select each companion, and use a discussion button. Choosing
the question must only fill the input; Send spends one minute. Confirm that the
companion explains benefits and costs without committing the plan. Inspecting
profiles and switching characters must leave mission time unchanged.

Across separate playthroughs, try the controlled crossing, the high track with
the optional grain guard, and holding the convoy. Check that the recap describes
each plan's actual cost and that aftermath questions make sense. After the
chapter ends, character inspection should remain available while conversation
buttons disappear. Portrait initials are expected until artwork is supplied.

The initial companions already have enough confidence to accept viable plans;
inspection is not a mandatory unlock. Automated reconsideration checks seed
uncertainty once, then use ordinary commands to resolve it: Matilda inspects the
ridge markers, and Horace examines the convoy petition. No further direct state
changes are used to secure acceptance. Reopening the save must preserve the
evidence, and examination alone must not commit a plan or spend guards.

Development playthroughs found that Horace's petition examination originally
did not supply the evidence requested by his conditional guard response. The
petition now records direct evidence that holding delays supplies, allowing the
guard request to be reconsidered. Browser coverage repeats this path across a
reload. These are local deterministic checks, not external tester feedback or
validation of live AI dialogue quality.

This build is ready for a short external playtest. The goal is to discover
where a new player becomes confused, not to teach them the intended solution.

## Give the tester

For the browser build, give the tester private access to the deployed game and
start with **New game**. The page explains the objective, clock and suggestions.
Do not coach them toward a particular NPC or ending. Selecting a suggestion
fills the input; Send submits it. All three outcomes should remain viable across
separate playthroughs, rather than requiring the tester to discover one solution.

For a source checkout, ask them to double-click `play.cmd`. For a packaged
build, give them `BadBadger.exe`. Do not explain the commands unless they ask;
the opening and `help` should be sufficient.

Ask the tester to play until the chapter ends. A typical run takes about 10–20
minutes. They may use OpenAI dialogue when `OPENAI_API_KEY` is already set, or
play the deterministic version with `play.cmd -Offline`.

## Observe

Record these without interrupting the session:

- the first moment the player does not know what to type;
- commands that seem reasonable but are rejected or misinterpreted;
- whether the player understands that the NPCs disagree and may act alone;
- whether the final choice feels informed, meaningful, and attributable;
- any output that exposes implementation details or feels repetitive;
- the ending they reached and whether the summary matches their understanding.

For the browser playability pass, also check:

- Can they explain which commands spend time and that NPCs may act independently?
- Do they recognize that a decisive request is irreversible if accepted?
- Does a refusal clearly state that no action happened, with a useful next step?
- Can they distinguish investigation, aftermath and a completed chapter?
- Does the recap attribute their accepted request without crediting them for an autonomous choice?
- Does an offscreen outcome stay undisclosed unless they learn it through play?
- Can they start over and explore a different outcome without losing their login?

These are playtest questions, not claims of completed external validation.
The automated suite checks the corresponding engine/presentation boundaries;
human observation is still needed to judge whether the guidance is clear.

## Ask afterward

1. What did you believe was happening?
2. What were you trying to accomplish?
3. Which character did you trust, and why?
4. Did anything happen that felt arbitrary or unfair?
5. Was there a moment when you felt stuck?
6. What consequence would you most want to explore afterward?

Start each tester from a fresh save with `play.cmd -NewGame`, and turn every
repeated confusion point into a regression test before changing the design.
