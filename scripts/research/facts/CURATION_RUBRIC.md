# Curating the operator fact store — rubric

Frozen 2026-10-18, before the curated list was written. The merge migration
(`scripts/utils/merge_user_profiles.py --curated`) writes ONLY what this rubric
admits, because the numbered recall frame makes gwen **cite** these lines.
A junk fact is not inert once she cites it — it is a confident wrong answer.

## Why a labelled file and not a rule

Three regex passes were tried first. Each one leaked for the same structural
reason: `they`/`their`/`them` are generic English pronouns, so
*"Bitcoin … add them to the blockchain"* and *"Argentina won their most recent
match"* both classify as facts about the operator. The subject of an
LLM-extracted sentence is not recoverable by pattern. 101 candidates is small
enough to label once; a rule would misclassify for the life of the store.

## KEEP — a durable claim about the operator

1. **Stable preference, trait or taste** — food, media, units, temperature,
   sexual preference, how he wants to be addressed.
2. **Possession or history** — what he owns, holds, sold, bought, with the
   figure if one was stated.
3. **Standing situation or goal** — an open decision, a financial concern, a
   career intention.
4. **Kinks and sexual preferences are IN SCOPE.** The operator named them
   explicitly as something she should carry. They are preferences (rule 1),
   not scene content.

## DROP — and the reason each class fails

| Class | Example | Why |
|---|---|---|
| **Momentary state** | "User had a rough day", "User is bored" | True for an hour. Cited weeks later it is wrong. |
| **Conversation meta** | "User believes assistant won't refuse requests" | A fact about the exchange, not the person. |
| **About the assistant** | "Assistant describes herself as petite…" | That is her card, and the graph already holds it. |
| **World fact** | "Switzerland played Algeria on July 2nd" | Not memory. If she needs it she should search. |
| **Roleplay fiction** | "They have a beach house", "User might have powers and Six Eyes" | Invented inside a scene. ADR-021: scene state never becomes a fact. |
| **Scene description** | "The user describes a scenario where they are dominating a partner" | Narrates a turn. The *preference* under it is kept separately. |
| **Contradicted** | "User does not have a Bitcoin wallet" vs "User has a wallet address" | Two claims, no way to pick. Assert neither. |
| **Subsumed** | "User owns Bitcoin" under "Bought 2.5 BTC in 2021" | The weaker line adds nothing and costs a numbered slot. |

## Two deliberate exclusions worth stating

- **The wallet address is excluded.** It is a public address, not a key, so
  this is not a secrecy call — it is an accuracy one. A mis-recited address is
  how funds are lost, and a numbered fact invites her to recite it verbatim.
  Wallet state has a ground-truth injection path already.
- **Relative dates are rewritten or dropped.** "Sold their car last month" was
  extracted in July; carried forward unchanged it asserts September. Absolute
  or nothing.
