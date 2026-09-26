# Backend routing evidence

This ledger contains only benchmark conclusions that have been accepted strongly enough to change a routing rule. Raw datasets, logs, traces, and candidate reports remain in the external evaluation archive.

No benchmark-backed routing rule has been promoted yet. Every current catalog row is therefore marked `heuristic`.

## Evidence entry contract

Each promoted entry must state:

- `evidence_id` and the affected `route_id`;
- the archived experiment path or receipt;
- representative task set and input ranges;
- operating system, processor architecture, accelerators and drivers, backend and framework versions, seeds, dataset versions, and relevant configuration;
- correctness checks and compared metrics, including wall time, throughput, memory, run-to-run variance, and stability when relevant;
- conclusion, limitations, and the exact routing change it supports.

Use an immutable `evidence_id`. A later experiment that changes the conclusion receives a new entry; do not rewrite the historical result.

## Promotion rules

- Compare candidates on the same representative tasks and correctness requirements.
- Separate missing or uncallable software from poor algorithmic performance.
- Do not generalize beyond the tested task class, input range, platform, accelerator, or backend versions.
- Promote a result only through the Skill evaluation workflow. A manual preference or isolated successful run remains `heuristic`.
- Add the evidence entry before changing a route to `benchmarked`, then cite its `evidence_id` from the catalog.

## Promoted evidence

None.
