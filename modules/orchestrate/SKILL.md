---
name: orchestrate
description: Apply the Workflow Orchestration methodology for complex, multi-step tasks — plan-first approach, subagent strategy, self-improvement loop, verification before done, elegant solutions, and autonomous bug fixing. Use for any non-trivial task with 3+ steps or architectural decisions.
category: engineering
tier: core
slash_command: /orchestrate
allowed-tools: Bash Read Edit Write Grep Glob Agent Task Workflow
---

# Orchestrate — Complex Task Orchestration

Apply workflow orchestration methodology for multi-step tasks with 3+ steps or architectural decisions.

## How to use
- `/orchestrate <task>` — plan-first approach for any non-trivial task

---

## Methodology

### 1. Plan first (invoke `writing-plans` skill)

**Always start by invoking the `writing-plans` skill** (or native plan mode if your platform has it) to produce a structured plan with TDD-style task steps. Write it to wherever `writing-plans` puts plans for this repo — do not assume a fixed path. Do not proceed to coding until the plan is written and reviewed.

- If something goes sideways mid-task: STOP, re-plan using `writing-plans` again
- Track progress via `tasks/todo.md` checkboxes

### 2. Use subagents (fan out independent work)
- Offload research, exploration, and parallel analysis to subagents
- Keep main context window clean and focused
- One task per subagent for precision
- Invoke the `subagents` skill for the **fan-out ladder** and **standard return contract**

Once the plan splits into **independent workstreams**, do not implement them serially.
Fan them out using the ladder from the `subagents` skill (Workflow tool → parallel
subagents → serial fallback):

```
plan (writing-plans)
  → parallel implementation subagents     # one per independent workstream, isolated
      each returns { summary, files[], diff, risks[], confidence }
  → gather the structured diffs
  → independent reviewer subagent          # fresh context, runs /code-review — no self-review bias
      returns { findings[]{severity,file,detail}, verdict }
  → merge / verify gate                    # apply, run /gate, only then mark done
```

Reference `dispatching-parallel-agents` and `subagent-driven-development` for the
mechanics. Use worktree isolation when subagents edit files in parallel so they don't
collide. Workstreams that share state (same module, ordering dependency) stay serial.

### 3. Self-improvement loop
- After any correction: note the pattern in `tasks/lessons.md`
- Review lessons at session start for relevant context

### 4. Verify before done
- Never mark complete without proving it works
- Run tests, check logs, diff behaviour vs main
- Ask: "Would a staff engineer approve this?"

### 5. Demand elegance
- After a working but messy fix: "Implement the elegant solution"
- Skip this for simple, obvious fixes

### 6. Autonomous bug fixing
- When given a bug: investigate and fix. Do not wait for hand-holding.
- Pair with `/fix` for CI failures and log-based bugs.

---

## Task Management

1. Write plan → `tasks/todo.md`
2. Check in before implementation starts
3. Mark items complete as you go
4. Document results in review section
5. Capture lessons → `tasks/lessons.md`
