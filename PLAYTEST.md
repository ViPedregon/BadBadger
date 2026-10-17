# Relay Crisis playtest

This build is ready for a short external playtest. The goal is to discover
where a new player becomes confused, not to teach them the intended solution.

## Give the tester

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

## Ask afterward

1. What did you believe was happening?
2. What were you trying to accomplish?
3. Which character did you trust, and why?
4. Did anything happen that felt arbitrary or unfair?
5. Was there a moment when you felt stuck?
6. What consequence would you most want to explore afterward?

Start each tester from a fresh save with `play.cmd -NewGame`, and turn every
repeated confusion point into a regression test before changing the design.
