# Contributing

The best contribution is a real machine→human boundary that currently lives in its own silo.

A connector should answer four questions clearly:

1. What exact condition means the machine cannot safely continue?
2. Which `human://` verb represents that contribution?
3. What small context snapshot lets a person decide without opening the source system?
4. How does the resolution resume the original workflow?

Please keep platform-specific semantics inside adapters and the core request model platform-neutral. New ranking inputs must affect surfacing only; they must not become implicit approval logic.

Run `pytest -q` before opening a PR.
