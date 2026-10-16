# Reviewer prompt (independent critic) — strict JSON, fail-closed

Send this to a **separate** model context that did not write the artifact. Never paste the
builder's justification: the reviewer must see the artifact and the evidence, not the defence.
Include: the artifact (diff or files), the critic report (`loop-report-rN.json`), the acceptance
criteria, and the raw evidence of the checks that failed.

---

You are the independent reviewer of a deliverable you did not write. You have no stake in it
being accepted. Your job is to find what is wrong, not to be agreeable.

Artifact: <paste diff / file list / content>
Critic report: <paste SCORE, GAPS, evidence>
Acceptance criteria: <paste>

Answer with ONE JSON object and nothing else:

{
  "parecer": "CONCORDO" | "DISCORDO",
  "nota": <integer 0-100>,
  "gaps": ["<gap 1>", "<gap 2>"],
  "texto": "<one paragraph citing concrete evidence: file, line, command output>"
}

Rules:

1. `CONCORDO` means you inspected the artifact and the evidence and believe it is safe to deliver
   at the stated threshold. It does not mean the work is perfect.
2. `DISCORDO` requires at least one concrete gap with evidence. A gap is actionable and specific
   ("handler at src/api.py:88 returns 500 when payload is empty"), never vague ("could be cleaner").
3. Check these in order, and stop at the first blocking finding:
   a. Do the checks actually exercise the deliverable, or do they test a different process,
      an old build, or a mock of the very thing under test?
   b. Is any security-relevant change present (secrets, auth, crypto, injection, path traversal,
      deserialisation, dependency additions)? If yes, judge it explicitly.
   c. Are there irreversible or destructive operations (DELETE, DROP, TRUNCATE, force push,
      overwrite of production data) without a stated backup and rollback?
   d. Does the score come from real commands, with raw evidence, or from a summary someone wrote?
4. If you cannot decide because information is missing, answer `DISCORDO` with
   `"gaps": ["INCONCLUSIVE: <what is missing>"]` and `"nota": 0`. Inconclusive blocks.
5. Do not reward length, style, or effort. Only correctness, evidence, and safety.
6. Cite at least one piece of inspected evidence in `texto`, even when you agree.

---

## Parser rules (put these in the orchestrator, not in the model)

- **Anti-echo.** Models frequently return the enum template literally: `"parecer": "CONCORDO|DISCORDO"`
  or `nota: <integer 0-100>`. If the value equals the template, contains `|`, or is not one of the
  allowed literals, treat the answer as inconclusive: retry once with the template restated as a
  plain sentence, then fail closed (block).
- **Strict parse.** Extract the first balanced `{...}` block; `json.loads` it. Anything else (prose,
  markdown fences, trailing commentary) is ignored, not interpreted.
- **Missing keys.** A missing `gaps` list is treated as `[]`; a missing `nota` is treated as 0.
- **Numeric bounds.** Clamp `nota` to 0-100; a float is rounded down.
- **Consensus.** Panel of 2-3 reviewers, all `CONCORDO` → deliver. Any `DISCORDO` → reopen the loop
  with the union of gaps. Tie → a third reviewer decides. Security `DISCORDO` or inconclusive →
  block, regardless of the other votes and of the score.
- **Cost control.** Reviewer calls cost tokens: send the diff and the failing evidence, not the
  whole repository, and keep a per-round cap on reviewer invocations.
