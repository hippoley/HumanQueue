# human:// brand contract

`human://` is the public project brand and protocol language.

The project should not present itself as a generic approval queue. A queue is one possible surface for a narrower infrastructure primitive: a machine has reached a boundary it cannot safely or correctly cross without a human contribution, and that contribution must return to the exact thing that is waiting.

## Naming hierarchy

| Layer | Canonical name | Role |
| --- | --- | --- |
| Public brand | `human://` | memorable project identity and protocol language |
| Core primitive | `HumanBoundary` | one addressable machine → human → machine boundary |
| Product category | human-boundary control plane | descriptive infrastructure category |
| CLI | `humanq` | compatibility surface |
| Python distribution | `human-queue` | compatibility surface |
| Python package | `humanqueue` | compatibility surface |
| Legacy SDK class | `HumanQueue` | supported alias of `HumanBoundary` |
| Environment | `HUMAN_QUEUE_*` | compatibility surface |
| Local state | `~/.human-queue/` | compatibility surface |

## Preferred language

Prefer:

- “send this boundary to `human://`”
- “create a `HumanBoundary`”
- “the human-boundary control plane”
- “project the boundary into Slack / Telegram / web”
- “return the decision to the exact paused action”

Avoid using “Human Queue” as the product name in new copy. “queue” should describe a UI or scheduling behavior, not the system itself.

## Compatibility policy

Brand migration must not force an integration migration.

Existing commands, package names, environment variables, config directories, wire fields and schema IDs remain valid until there is a separately documented major-version migration. New documentation may prefer the new primitive while old code continues to run.

Python:

```python
from humanqueue import HumanBoundary

human = HumanBoundary()
```

Existing code remains valid:

```python
from humanqueue import HumanQueue

human = HumanQueue()
```

JavaScript:

```js
import { HumanBoundary } from "./sdk/js/humanqueue.mjs";
```

`HumanQueue` remains exported.

## What the brand promises

The brand is only credible if the implementation preserves these invariants:

1. identify the authoritative waiting source/session/request without guessing;
2. carry bounded decision context rather than a whole private transcript by default;
3. enforce the intended resolver boundary;
4. make expiry, supersession and duplicate resolution explicit;
5. return one human decision to one exact waiting action;
6. distinguish delivery from semantic resume confirmation.

If a runtime-local feature solves the boundary completely, `human://` should get out of the way.


## README visual grammar

The README should read like a product surface, not an accumulated manual.

### Visual roles

- warm off-white / near-black backgrounds: infrastructure, not SaaS gloss;
- acid-lime: the active `human://` boundary or a successful exact return;
- signal orange: a machine is blocked and needs human contribution;
- monospace labels: protocol identity, session identity, receipts, and machine state;
- sans-serif copy: human explanation and decision context.

Illustrations should explain a real contract:

1. **hero topology** — many autonomous sources converge on one addressable human boundary;
2. **decision card** — the human sees why the machine stopped, the authoritative source identity, and explicit actions;
3. **exact-resume lifecycle** — delivery is not the end; the return path is part of the product.

Avoid decorative AI imagery, robot stock art, generic gradient clouds, and screenshots that cannot be reproduced from the actual product.

### Reading rhythm

A new visitor should move through the README in this order:

```text
recognize the problem
        ↓
see the boundary visually
        ↓
understand the human interaction
        ↓
run the loop in 60 seconds
        ↓
learn the primitive
        ↓
connect a real runtime
        ↓
inspect evidence / safety / deeper internals
```

Use deliberate vertical whitespace between these transitions. Secondary implementation detail belongs in `<details>` blocks or dedicated docs rather than interrupting the first-pass narrative.

The first screen should always answer:

- What is `human://`?
- Why is it different from a native approval prompt?
- What can I click or run right now?
