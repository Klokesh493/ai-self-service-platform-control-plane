"""Catalog request validation and Kubernetes bundle rendering with quota accounting."""
import json
from typing import Annotated, List, Literal

from pydantic import Field, model_validator

from .agent_loop import Probe
from .contracts import Count, Positive, Record, unique

TITLE = "AI Self-Service Platform Control Plane"
BRIEF = "Review a service request against catalog policies and current tenant quota usage."
SOURCES = ["request", "catalog", "usage"]
DNSName = Annotated[str, Field(pattern=r"^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$")]


class ServiceRequest(Record):
    name: DNSName
    namespace: DNSName
    owner: DNSName
    template: Literal["api", "worker"]
    replicas: Positive = Field(le=50)
    cpu_millis: Positive = Field(le=32000)
    memory_mib: Positive = Field(le=131072)
    image: str = Field(pattern=r"^[a-z0-9.-]+/[a-z0-9./-]+@sha256:[a-f0-9]{64}$", max_length=220)
    port: int = Field(strict=True, ge=1024, le=65535)


class Catalog(Record):
    namespace: DNSName
    owner: DNSName
    approved_registries: List[str] = Field(min_length=1, max_length=10)
    cpu_quota_millis: Positive
    memory_quota_mib: Positive
    replica_quota: Positive
    max_service_replicas: Positive
    allowed_ports: List[int] = Field(min_length=1, max_length=10)


class Usage(Record):
    name: DNSName
    replicas: Count
    cpu_millis: Count
    memory_mib: Count


class Case(Record):
    request: ServiceRequest
    catalog: Catalog
    usage: List[Usage] = Field(max_length=100)

    @model_validator(mode="after")
    def identities(self):
        unique([row.name for row in self.usage], "service names")
        if any(not value or "/" in value or ":" in value for value in self.catalog.approved_registries):
            raise ValueError("approved registries must be plain hostnames")
        return self


def prepare(case):
    source = case.model_dump()
    probes = [Probe(key, "Read " + key + " evidence", lambda key=key: source[key])
              for key in SOURCES]

    def decide(evidence):
        request, catalog = case.request, case.catalog
        # Exclude the old allocation for an update before adding its replacement.
        others = [row for row in case.usage if row.name != request.name]
        totals = {"cpu_millis": sum(r.cpu_millis * r.replicas for r in others)
                  + request.cpu_millis * request.replicas,
                  "memory_mib": sum(r.memory_mib * r.replicas for r in others)
                  + request.memory_mib * request.replicas,
                  "replicas": sum(r.replicas for r in others) + request.replicas}
        blockers = []
        if request.namespace != catalog.namespace or request.owner != catalog.owner:
            blockers.append("request does not match catalog tenant or owner")
        if request.image.split("/", 1)[0] not in catalog.approved_registries:
            blockers.append("image registry is not approved")
        if request.port not in catalog.allowed_ports:
            blockers.append("port is not in the catalog allowlist")
        if request.replicas > catalog.max_service_replicas:
            blockers.append("service replica cap exceeded")
        if totals["cpu_millis"] > catalog.cpu_quota_millis:
            blockers.append("tenant CPU quota exceeded")
        if totals["memory_mib"] > catalog.memory_quota_mib:
            blockers.append("tenant memory quota exceeded")
        if totals["replicas"] > catalog.replica_quota:
            blockers.append("tenant replica quota exceeded")
        return {"decision": "provision_ready" if not blockers else "provision_hold",
                "ready": not blockers, "citations": SOURCES, "blockers": blockers,
                "request": request.model_dump(), "projected_usage": totals,
                "reason": "Request fits the catalog, image and quota policy." if not blockers
                          else "Correct the request or refresh capacity evidence before exporting."}

    return probes, decide


def bundle(report):
    request = report["request"]
    labels = {"app": request["name"], "owner": request["owner"]}
    container = {
        "name": request["name"], "image": request["image"],
        "ports": [{"containerPort": request["port"]}],
        "resources": {"requests": {"cpu": f"{request['cpu_millis']}m",
                                  "memory": f"{request['memory_mib']}Mi"},
                      "limits": {"cpu": f"{request['cpu_millis'] * 2}m",
                                 "memory": f"{request['memory_mib'] * 2}Mi"}},
        "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                            "capabilities": {"drop": ["ALL"]}},
    }
    deployment = {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": request["name"], "namespace": request["namespace"], "labels": labels},
        "spec": {"replicas": request["replicas"], "selector": {"matchLabels": {"app": request["name"]}},
                 "template": {"metadata": {"labels": labels}, "spec": {
                     "automountServiceAccountToken": False,
                     "securityContext": {"runAsNonRoot": True, "runAsUser": 10001,
                                         "seccompProfile": {"type": "RuntimeDefault"}},
                     "containers": [container]}}},
    }
    files = {"deployment.json": json.dumps(deployment, indent=2)}
    if request["template"] == "api":
        service = {"apiVersion": "v1", "kind": "Service",
                   "metadata": {"name": request["name"], "namespace": request["namespace"]},
                   "spec": {"selector": {"app": request["name"]},
                            "ports": [{"port": request["port"], "targetPort": request["port"]}]}}
        files["service.json"] = json.dumps(service, indent=2)
    return files
