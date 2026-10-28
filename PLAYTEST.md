# Relay Crisis playtest

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
