# Measured performance and evaluation

These are local probes, not capacity guarantees. The reference environment is Linux aarch64 with Python 3.12, SQLite on the temporary filesystem, default detectors/redacted capture, 2,040-byte benign message content, four optional feature consumers, and ten events per workflow. Filesystem and CPU behavior will differ on deployment hardware. Full machine specifications and a production storage reference remain to be established.

Load measurements used core revision `aeae2cc`; policy measurements used `96b2cf2`. JSON reports preserve the measured quantiles, counters, and workload parameters. Later callback/privacy fixes do not establish a new capacity measurement.

| Probe | Completed events/s | p95 admission per batch | p95 publish to committed projection | Sampled peak RSS |
| --- | ---: | ---: | ---: | ---: |
| [1,000 events, individual admission](validation/load-single.json) | 117.44 | 17.50 ms | 65.85 ms | 42.3 MiB |
| [1,000 events, batches of 100](validation/load-batch.json) | 213.93 | 152.69 ms | 3,184.15 ms | 48.1 MiB |
| [3,000 events at 50/s, 60 seconds](validation/load-paced.json) | 49.97 | 4.90 ms | 13.12 ms | 43.5 MiB |

Every probe finished with zero pending events, dead letters, consumer errors, or drops; all four consumers received every event. The burst batch test increases throughput at the expense of queueing latency. Batch admission quantiles describe a whole batch and are not per-event latency.

Timing begins immediately before publication, includes acceptance, and ends after the projection/checkpoint transaction commits. It measures analysis availability on benign events, not detection recall or alert delivery latency for an attack. Analysis-only quantiles are also reported. RSS samples cover admission and drain and may miss very short peaks. JSONL export completion is included in total completion time.

For [100 policy proposals](validation/policy-latency.json), deterministic policy evaluation p95 was 0.006 ms; full durable proposal p95 was 58.096 ms. The latter includes journal processing, export drain, and decision auditing. No external tools were executed. These measurements deliberately exclude model judges, human waiting, remote services, and tool execution.

## Graph traversal in 0.2.1

The graph now revisits only affected descendant actions and builds ancestor paths on demand. Wait-cycle detection also visits each dependency once instead of repeatedly traversing shared branches.

The [graph-only probe](validation/graph-traversal-0.2.1.json) processes 150 linked read-tool starts in one workflow. Three local samples had median elapsed times of 0.508 seconds for the `f5b609e` graph module and 0.0124 seconds for `0cbe883`, approximately 41 times faster on this specific workload. Both modules use the current event/finding schemas and produce no findings for the benign chain. Event construction, SQLite, detectors, checkpoint serialization, exports, and consumers are excluded. This is a microbenchmark; it does not establish a corresponding gain in application throughput or certify the sustained-load target.

## Reproduce

```bash
python tools/benchmark.py --events 1000 --output single.json
python tools/benchmark.py --events 1000 --batch-size 100 --output batch.json
python tools/benchmark.py --events 3000 --rate 50 --output paced.json
python tools/benchmark_policy.py --actions 100 --output policy.json
python tools/benchmark_graph.py --baseline f5b609e --events 150 --samples 3 --output graph.json
fisheye evaluate --split all --output evaluation.json
```

`--workflow-size`, `--rate`, `--batch-size`, and `--timeout` expose the workload. A rate is the offered schedule, not a guarantee that the producer maintains it. Benchmark output includes actual admission/completion time and throughput. All data is created in a temporary directory and removed after the run.

Run the graph probe from a Git checkout. Its optional `--baseline` loads that revision's graph module, so use a trusted local revision. The report identifies both revisions and records each sample; omitting the baseline measures only the current graph.

## Detection regression corpus

The [recorded regression metrics](validation/regression-metrics.json) cover seven handcrafted workflows: propagated instructions, benign quotation, an unlinked action, late source delivery, sensitive transfer, authorized transfer, and a wait cycle. Two cases are labeled tuning and five test; these are related regression fixtures, not independently collected held-out data.

All seven match their assessed categories. For injection propagation there are two true positives; for data movement and coordination, one each. The three benign workflows produce zero findings in the assessed categories. Other detector categories are reported but not scored. These tiny counts do not establish production precision/recall, calibrated confidence, or a universal prompt-injection detection rate.

## Outstanding release targets

The design's 1,000 events/s for 30 minutes target is **not met or certified** by these results. A 24-hour wall-clock soak has **not been run**. The current tests cover bounded histories, expiry, retention, restart, duplicate delivery, rollback, and a process exit during a projection transaction; those checks do not substitute for a long-duration soak.

Next scaling work should profile large single-workflow checkpoints and the remaining full-state scans, move incremental graph projections out of checkpoint snapshots, establish a persistent-storage reference machine, and run sustained backlog/retention tests. Expand the independently labeled corpus and native streaming/handoff/cancellation coverage before broadening the supported deployment claim.
