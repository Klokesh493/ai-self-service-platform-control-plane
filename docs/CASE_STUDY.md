# Engineering case study

## Engineering problem

A self-service request can pass its per-service limits while exhausting tenant quota or selecting an unapproved image registry.

## Design choices

Update accounting removes the old allocation for the service before adding its replacement. Images require a digest and an exact approved registry hostname. Exported pods run as a non-root user with dropped capabilities, a read-only root filesystem, and no mounted service-account token.

## Implementation

I built a service catalog control plane that validates owner and namespace, immutable image references, ports, replica limits, and projected tenant resource usage. Approved requests export hardened Kubernetes Deployment and Service documents.

The API and CLI share ops_platform.service.evaluate. Domain contracts and decisions live in ops_platform/domain.py. SQLite persistence is implemented in ops_platform/storage.py. Prometheus metrics are created per API instance. The deployment directory contains the supplied observability and Kubernetes configuration.

## Technical challenges

Wrong ownership, an unapproved registry, an unexpected port, excess service replicas, or exhausted tenant quotas holds provisioning.

Retry handling also matters across all workflows. A duplicate client request reuses its report, while stale approvals fail with HTTP 409. An unavailable model records an incomplete report that cannot be approved. HEC failures leave audit events pending.

## Validation results

6 labeled domain scenarios passed. The actual local Qwen run returned provision_ready for ready.json and provision_hold for hold.json. Captured reports include the model name, probe trace, content digests, and cited evidence. API and domain tests cover the decision policy and workflow failure cases. [Validation details](VALIDATION.md)

## Lessons learned

A useful golden path needs explicit ownership and resource accounting, including the replacement semantics of updates.

## Remaining scope

Catalog and usage data are caller-supplied snapshots. The platform does not reserve live cluster capacity or apply manifests. Two concurrent different requests can each pass against the same snapshot, so an external reconciliation controller must serialize real cluster reservations.
