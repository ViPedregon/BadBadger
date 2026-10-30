# Playable beta roadmap and current assessment

1. **Relay playability:** implemented and exercised, including engine validation,
   saves/resume, repeated-turn protection, new game and aftermath. It remains a
   prototype and regression scenario rather than the intended fantasy campaign.
2. **Conversation and knowledge:** implemented with scoped NPC context, memories,
   hearsay, explicit action consent and secret filtering. Generated prose can
   still hallucinate or paraphrase secrets; semantic secrecy is not guaranteed.
3. **Persistent identities:** implemented using approved Victor, Matilda and Horace
   lore snapshots. Future romance and Elena continuity have not been advanced.
4. **Representative beta chapter:** implemented as The North Road, three branches
   and a guard follow-up. This is a short slice, not a complete campaign.
5. **Complete the player experience — current stage:** journal, recap, mobile
   layout, interrupted-request recovery and new-game archives exist. Reusable
   authored delegation phrases and public objective stages now explain follow-up
   work. Player-safe archive browsing/resume now preserves the current game before
   switching, with atomic revisions, transcript recovery and retry protection.
   Raw world state, NPC secrets and developer inspection remain server-side.
   This completes the planned core features for this stage; broader usability
   improvements should follow observed beta feedback.
6. **Prepare external testing:** offsite backups with a restore drill, monitoring,
   AI usage/cost controls, feedback collection and isolated tester saves/access.
   Local backup tooling exists; operational deployment of these is not verified.
7. **Small closed beta:** observe real sessions and improve confusing interactions
   before extending the campaign or adding arbitrary task planning.

The delegation work is a small foundation for stages 4–5. It does not make
unrestricted natural-language tasks possible. The next stage is external-test
preparation, beginning with offsite backup/recovery and isolated tester access.
Protect save integrity and engine authority now; exhaustive dialogue,
extra chapter content and long-running companion work can follow observed need.

Automated local checks establish implementation behavior. They do not establish
the deployed Render version or private hosting settings. Content additions apply
to new games because existing saves retain their authored scenario snapshot.
