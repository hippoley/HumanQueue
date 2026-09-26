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
