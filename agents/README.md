# Role contracts

Roles describe responsibility, inputs, outputs, and stopping conditions. They are not long-running processes and do not imply one model call per role. One capable session may perform several roles when that is cheaper and safe.

The initial roles exist because they own distinct decisions:

- `researcher.md` gathers external evidence when repository evidence is insufficient.
- `product-analyst.md` turns an idea into bounded requirements and acceptance criteria.
- `architect.md` chooses system boundaries and records consequential decisions.
- `reviewer.md` independently checks changes against requirements and evidence.

Implementation remains the responsibility of the active engineering session for now. A separate implementer agent would add ceremony without a routing system or measured need.

Every role returns a written artifact or a structured result. Roles may recommend actions, but tools perform commands and file operations.
