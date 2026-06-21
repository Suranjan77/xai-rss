# Feature Proposal

Ideas for evolving `idigest` from a daily digest into a genuine personal learning system.
Each is rated by **value** (to the stated goal — learning interpretability research with
little time) and rough **effort**. Ordered roughly by value-for-effort.

> Goal recap: learn and keep up with interpretability research in byte-sized pieces, with
> depth on demand, ideally while walking.

---

## Tier 1 — high value, low/medium effort

### 1. Spaced-repetition review
After a paper is "read", schedule short recall prompts (1 day, 1 week, 1 month) generated
from its `key_insight`/`depth`. Delivered as a tiny section in the daily email or a
"Review" tab. Turns passive reading into retention — the single biggest lever for
*learning* vs. *consuming*. **Effort:** medium (a `reviews` table + scheduler + prompt).

### 2. Reply-to-the-email Q&A
Let the user reply to the daily email with a question; an IMAP poller feeds it (plus the
paper context) to Gemma and replies. "Ask while walking" without opening the UI.
**Effort:** medium (IMAP poll + threading).

### 3. Weekly digest + progress
A Sunday email: what you learned this week, what's next on the path, your "interesting"
shortlist, and 2–3 notable new arrivals. Gives momentum and a sense of a curriculum.
**Effort:** low.

### 4. Private podcast feed
Expose an RSS feed of the generated audio (enclosures point at `/audio/{id}.mp3` over the
tailnet). Subscribe once in any podcast app → automatic offline download, queue, speed
control, resume — ideal for walks. The audio already exists; this just adds a feed.
**Effort:** low.

### 5. "Why this next?" provenance
On each paper, show *which prerequisites* placed it here and *what it unlocks* (it's all
in the concept graph already). Makes the learning path legible and trustworthy.
**Effort:** low.

---

## Tier 2 — high value, more effort

### 6. Adaptive difficulty / pacing
Track read/skip/interesting signals and the time-of-day you actually open emails. Adjust
how many papers/week and their difficulty, and re-rank the path toward topics you engage
with. **Effort:** medium–high.

### 7. Concept pages (not just papers)
Aggregate per-concept: definition, the papers that teach it, and how it connects. A
"textbook view" auto-built from the graph — browse by *idea* rather than by paper.
**Effort:** medium.

### 8. Multi-track learning paths
Parallel ordered tracks (e.g. *feature attribution*, *mechanistic interpretability*,
*evaluation/faithfulness*) so the user can pick a lane instead of one global line.
**Effort:** medium (topic-scoped topological sorts).

### 9. Interactive graph UI
A visual concept/paper graph (prerequisites, citations, similarity) to explore the field
spatially. Great for orientation. **Effort:** high (front-end).

---

## Tier 3 — nice to have / exploratory

### 10. Code & demo extraction
When a paper has a repo (arXiv → Papers-with-Code / GitHub), surface a runnable minimal
example or a one-paragraph "how to try it". **Effort:** medium.

### 11. Citation-aware ingestion
Follow citations from high-value papers (especially seed papers) to discover foundational
or follow-up work automatically, not just keyword search. **Effort:** medium.

### 12. Two-host audio mode
Optional NotebookLM-style dialogue (host + skeptic) for more engaging listening, using two
F5-TTS reference voices. **Effort:** medium.

### 13. Highlights & notes
Let the user highlight/annotate in the UI; fold notes into the spaced-repetition prompts
and the weekly digest. **Effort:** medium.

### 14. Multi-source ingestion
Add OpenReview (ICLR/NeurIPS), the Alignment Forum, and lab blogs (Anthropic, DeepMind)
to broaden beyond arXiv. **Effort:** medium.

### 15. Quality/feedback loop
A 👍/👎 on each email's explanation that tunes prompts (few-shot exemplars) or flags
papers to regenerate. **Effort:** medium.

---

## Operational hardening (not features, but worth doing)
- **Pre-generate** the next paper's content during the 06:30 ingest so the 07:00 email is
  instant (no audio wait).
- **Backups** of the SQLite DB + generated audio/figures (a nightly copy).
- **Health email** if a job fails (ingest/email/LLM down).
- **Model A/B**: keep prompts model-agnostic so a future local model can be swapped in.

---

## Recommendation
Start with **Tier 1**: spaced repetition (#1), the podcast feed (#4), and the weekly
digest (#3). Together they convert the tool from "a paper a day" into an actual
**curriculum with retention** — the highest-leverage step toward the goal, and all build
directly on what already exists.
