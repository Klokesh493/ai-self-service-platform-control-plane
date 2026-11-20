import json
from pathlib import Path

import pytest

from ops_platform import domain
from ops_platform.service import evaluate

def fixture(name):
    return json.loads((Path("examples") / (name + ".json")).read_text())


def test_update_replaces_old_allocation():
    report = evaluate(fixture("ready"))
    assert report["projected_usage"] == {"cpu_millis": 700, "memory_mib": 768, "replicas": 4}

def test_pod_security_and_selector():
    files = domain.bundle(evaluate(fixture("ready")))
    deploy = json.loads(files["deployment.json"])
    pod = deploy["spec"]["template"]
    assert deploy["spec"]["selector"]["matchLabels"]["app"] == pod["metadata"]["labels"]["app"]
    assert pod["spec"]["automountServiceAccountToken"] is False
    container = pod["spec"]["containers"][0]
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert container["resources"]["requests"]["cpu"] == "250m"

def test_worker_bundle_has_no_service():
    source = fixture("ready")
    source["request"]["template"] = "worker"
    assert set(domain.bundle(evaluate(source))) == {"deployment.json"}

def test_mutable_image_and_bad_dns_names_rejected():
    source = fixture("ready")
    source["request"]["image"] = "registry.example.com/checkout:latest"
    with pytest.raises(ValueError):
        evaluate(source)
    source = fixture("ready")
    source["request"]["namespace"] = "../other"
    with pytest.raises(ValueError):
        evaluate(source)
