from humanqueue import HumanBoundary

human = HumanBoundary()
decision = human.ask(
    "human://approve",
    source="my-agent", ref="run-42", title="Deploy to production?",
    why_now="The release passed CI and cannot continue without approval.",
    risk=.85, downstream=4, seconds=8,
    wait=True,
)
print(decision)
