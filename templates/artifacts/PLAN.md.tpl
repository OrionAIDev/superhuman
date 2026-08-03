# Implementation plan: {{project_name}}

> **For agentic workers:** Implement this plan task-by-task — prefer subagent-driven development (dispatch a fresh subagent per task), or execute it sequentially with a review checkpoint between tasks.

**Goal:** {{one_sentence}}

**Architecture:** {{2_3_sentences}}

**Tech Stack:** {{key_technologies_and_libraries}}

**Chunking strategy:** value-first | foundation-first | hybrid
**Value definition:** {{from_G3}}

---

## File structure (what gets built where)

<!-- Map of files to be created/modified; Architect's spine. -->

## Chunks

### Chunk 1: {{title}}

**Files:**
- Create: `<path>`
- Modify: `<path>:<lineranges>`
- Test: `<path>`

**Acceptance criteria:**
- <bullet>
- <bullet>

**Steps:**
- [ ] **Step 1: <action>**
  <code or command>
- [ ] **Step 2: <action>**
  <code or command>
- [ ] **Step 3: Commit**
  ```bash
  git add <files>
  git commit -m "<message>"
  ```

<!-- Continue for chunks 2..N. Per-chunk: TDD steps; bite-sized; no placeholders. -->
