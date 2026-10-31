# Conversation and knowledge boundaries

The Python engine owns consequences. AI responses are proposals, including
belief changes, movement, cooperation, relationship changes and disclosures.
This pass strengthens those boundaries without replacing the engine or adding
the future Victor/Matilda/Horace story content.

## Permissions and consequences

- NPC context has an engine-set `interaction_mode`. Player speech, dialogue
  history and memory cannot turn a conversation into an autonomous decision,
  even by quoting `ENGINE DECISION TICK` or claiming to be developer instructions.
- Consequential interactions require a direct affirmative request matching the
  authored action label after the NPC address. Negated, hypothetical, quoted or
  explanatory mentions do not authorize it. Polite requests such as “Could you
  broadcast the distress packet?” are supported. Ambiguous phrasing is rejected
  conservatively; the suggested command gives an accepted explicit form.
- A conversational movement proposal separately requires an explicit movement
  request naming an offered destination. Autonomous decisions retain their own
  validated route/action path and do not need player permission.
- One relationship proposal per target is accepted in an exchange, retaining
  the ±0.25 limit. Duplicate proposals cannot multiply it. Structured model
  output allows at most one action, four belief updates, four relationship
  updates and two disclosure requests. Duplicate belief keys and disclosure ids
  are also handled once per exchange.
- Disclosure permission is captured before the exchange. An action or attitude
  change cannot raise trust and reveal a previously withheld secret within that
  same exchange. The engine still rechecks the actual disclosure owner, location
  and current trust when supplying its authored content.

## Claims, evidence and recollection

A structured belief proposal must provide `source_quote`, copied from an
assertive player statement. The engine rejects missing/incorrect quotations,
questions, hypothetical prompts, unknown subjects and disclosure-protected belief
keys. Grounding is lexical, not semantic verification: accepted statements may
still be false. They remain hearsay attributed to the player, calibrated by the
NPC's trust. They never overwrite objective facts.

For evidence scoring, repeated hearsay from the same source about the same value
contributes only its strongest confidence. Repeating a claim is not independent
corroboration. Contradictions still reduce confidence and evidence remains
append-only. Existing saves recompute derived confidence at startup under this
rule; no evidence or authored identity is deleted.

Successful conversations create sourced episodic memories for the addressed
NPC and the player. The NPC remembers both what the player said and its own
reply, allowing an earlier promise or disagreement to survive the recent-dialogue
window and a server restart. Other NPCs do not inherit those memories. Legitimate
NPC-to-NPC sharing remains a separate validated operation.

Context retrieval ranks up to 100 salience-ordered memory candidates using
salience, topic overlap and recency, selecting at most eight. Memory text and
recent dialogue each have a 4,000-character prompt budget. Evidence includes
the last four entries plus its strongest entry, with bounded detail text; the
full record stays in SQLite. Retrieval is bounded and does not promise perfect
recall of every old exchange. The offline backend marks hearsay as unconfirmed;
the AI prompt instructs attribution, uncertainty and consistent character voice.

## Protected information

Disclosure contents and private/secret biography summaries are not model context.
Disclosure-protected belief keys are excluded even if an old save contains them.
Context projection also filters/redacts known protected text in historical
dialogue, memories, evidence detail, biography guidance and nested parameters.
This prevents a legacy leak or a player repeating a correct guess from becoming
trusted NPC knowledge on subsequent turns. The player's current exact input is
still supplied as untrusted player speech, not as authored truth.
Biography fragment ids and retrieval diagnostics remain in engine history;
the model receives only selected guidance, visibility and certainty.

Generated dialogue is checked for normalized literal echoes of authored
disclosure content, string-valued secret phrases and private/secret biography
summaries. A blocked response becomes a neutral refusal; its proposals are not
applied and it spends no mission time. The same protection covers proactive
speech. Protected belief keys cannot be laundered through autonomous belief
sharing. Ordinary NPC conversation cannot expose JSON inspection dumps, and
explicit requests for system prompts/hidden engine records are refused without
forwarding the request to the NPC backend.

Authorized disclosure content is supplied separately by the engine after its
checks, and becomes sourced player knowledge. Model clients receive neither
application credentials nor a database handle. Debug logging does not dump raw
structured model responses.

These protections are **not a semantic secrecy guarantee**. Literal matching
can miss paraphrases, inferred information and numeric guesses; numeric values
alone are not globally censored because that would hide ordinary mission times.
Pattern-based inspection detection is likewise limited. Free-form AI dialogue
can still hallucinate or claim unsupported certainty. Engine validation protects
canonical state and permissions; tests cannot prove every future model sentence
will be appropriate. Preserve that distinction when evaluating beta behavior.

## Validation

`test_knowledge_boundaries.py` exercises adversarial consent, mode spoofing,
unrequested movement, secret context pollution and echoes, authorized disclosure,
same-exchange trust escalation, duplicate updates, hearsay repetition, persistent
recollection, character isolation and context budgets. HTTP tests cover an
overeager model receiving the full action label in an information question and
a model attempting to echo a protected phrase into the response/transcript.
Existing browser, save/resume, backup/restore and three-outcome tests remain part
of the suite. Provider behavior is simulated; no live OpenAI key is required or
used by the automated suite.
