# CraneAssist project rules

- Preserve the user's four-layer, 16-class A1-D4 codebook. Do not substitute legacy 11-class codes.
- Keep source text immutable; preserve source URL, origin type, paragraph IDs and hashes.
- Never feed prior AI/human labels to a fresh evidence-generation run.
- Keep model candidates separate from human review. Unknown is not a negative label.
- Structural checks and exact quotes do not establish semantic correctness or expert truth.
- Never change expert validation, independent annotation or training/evaluation admission automatically.
- Keep secrets in process environment or ignored .env files. Never print or export them.
- Live model calls require the user's provider authorization; the current session authorized existing DeepSeek.
- Use meaningful tests for evidence corruption, state integrity and review/export behavior.
- Work is stored within this project. Do not overwrite thesis originals.
