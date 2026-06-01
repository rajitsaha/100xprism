---
name: enterprise-design
description: Produce a comprehensive technical blueprint for a web product or SaaS, suitable for implementation in Figma Make, engineering sprints, and cloud deployment.
category: design
tier: on-demand
slash_command: /enterprise-design
model: opus
---

# Enterprise Design — Technical Blueprint Generator

Produce a comprehensive technical blueprint for a web product or SaaS, suitable for implementation in Figma Make, engineering sprints, and cloud deployment.

## How to use
- `/enterprise-design <product or feature>` — full blueprint
- `/enterprise-design ia` — information architecture + sitemap only
- `/enterprise-design api` — API surface definition only
- `/enterprise-design data` — data architecture + entity model only
- `/enterprise-design ux` — user journeys + component inventory only
- `/enterprise-design stack` — tech stack recommendation only
- `/enterprise-design review` — audit current project against enterprise standards

---

## Step 0 — Load context

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)
INSTRUCTION_FILE=$(for f in CLAUDE.md AGENTS.md .cursorrules .windsurfrules .github/copilot-instructions.md GEMINI.md; do [ -f "$PROJECT_ROOT/$f" ] && echo "$PROJECT_ROOT/$f" && break; done)
[ -n "$INSTRUCTION_FILE" ] && head -100 "$INSTRUCTION_FILE"
```

Establish: site/product type, primary audience, core capabilities (3–5), technical priorities.

---

## Deliverables

Produce a structured technical blueprint covering:

### 1. Information Architecture
Complete sitemap with primary, secondary, semantic, and neutral path hierarchy. URL conventions (kebab-case, hierarchy reflects ownership, pagination via query params).

### 2. User Journey Mapping
Three critical conversion paths: acquisition→activation, free→paid, core workflow loop. Include drop-off points and success metrics.

### 3. Data Architecture
Entity relationships and schema models. Indexing strategy (FK indexes, composite for pagination, GIN for full-text). Caching tier (Redis key patterns + TTLs). Analytics tier (event schema for BigQuery).

### 4. API Surface Definition
Core REST endpoints (auth, primary entity CRUD, billing, admin). Standard response envelope. Third-party integrations table. Rate limiting per tier.

### 5. Component Inventory (30+)
Layout, navigation, data display, form, feedback, and feature-specific components. For each: purpose + key props.

### 6. Page Blueprints
Structural wireframe descriptions for: landing page, dashboard, detail/entity view, settings page.

### 7. Technology Stack
Recommended stack with rationale for: frontend, styling, state, backend, database, cache, auth, payments, email, hosting, CI/CD, observability, IaC.

### 8. Performance Benchmarks
Core Web Vitals targets (LCP < 1.8s, INP < 100ms, CLS < 0.05). API latency targets (P50/P95). Performance budget per page.

### 9. SEO Framework
URL conventions, meta structure per page type, schema markup strategy, Core Web Vitals for SEO.

### 10. Enterprise Considerations (if applicable)
Domain-Driven Design bounded contexts, API governance, zero-trust security, multi-region DR. Only include if product is at scale (> 10K users / multi-team / regulated).

---

## Output Format

Structured specification with clear headings, bullet points, and numbered lists throughout. Suitable for direct handoff to Figma Make or engineering sprint planning.
