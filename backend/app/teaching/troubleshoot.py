"""Signal extraction for troubleshooting: stack-trace frames, error lines and well-known platform failure hints."""
from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db import Database

PY_FRAME = re.compile(r'File "([^"]+)", line (\d+)(?:, in (\S+))?')
GO_FRAME = re.compile(r"(?:^|[\s(\[])([\w./\\-]+\.go):(\d+)")
JAVA_FRAME = re.compile(r"at ([\w.$<>]+)\(([\w$]+\.(?:java|kt|scala)):(\d+)\)")
JS_FRAME = re.compile(r"at (?:(\S+) )?\(?([\w./\\:-]+\.(?:js|ts|jsx|tsx|mjs)):(\d+)(?::\d+)?\)?")
OTHER_FRAME = re.compile(r"([\w./\\-]+\.(?:sh|bash|yaml|yml|tpl)):(\d+)")

ERROR_LINE = re.compile(
    r"\b(error|err|fail(?:ed|ure|ing)?|panic|fatal|exception|denied|forbidden|refused|timeout|timed out|unauthorized|"
    r"not found|invalid|crashloop\w*|oomkilled|evicted|unhealthy|backoff|traceback)\b", re.I)
NOISE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*|\b[0-9a-f]{8,}\b|\b\d+\b", re.I)
GENERIC_WORDS = re.compile(r"\b(error|err|failed|failure|failing|fail|exception|traceback|fatal|warning|info)\b", re.I)

# (pattern, label, what it usually means, commands to run next)
HINTS: list[tuple[re.Pattern, str, str, list[str]]] = [
    (re.compile(r"CrashLoopBackOff", re.I), "CrashLoopBackOff",
     "A container starts, exits and is restarted repeatedly. The reason is in the previous container's logs and exit code.",
     ["kubectl -n <ns> get pod <pod> -o wide",
      "kubectl -n <ns> logs <pod> -c <container> --previous",
      "kubectl -n <ns> describe pod <pod>"]),
    (re.compile(r"ImagePullBackOff|ErrImagePull|manifest unknown|pull access denied", re.I), "Image pull failure",
     "The node cannot pull the image: wrong tag, missing registry credentials, or the registry is unreachable.",
     ["kubectl -n <ns> describe pod <pod>   # read the Events section",
      "kubectl -n <ns> get pod <pod> -o jsonpath='{.spec.containers[*].image}{\"\\n\"}{.spec.imagePullSecrets}'",
      "kubectl -n <ns> get secret <pull-secret> -o jsonpath='{.type}'"]),
    (re.compile(r"OOMKilled|exit code:? ?137|Exit Code:\s*137", re.I), "OOMKilled / exit 137",
     "The container exceeded its memory limit and was killed by the kernel.",
     ["kubectl -n <ns> get pod <pod> -o jsonpath='{.status.containerStatuses[*].lastState.terminated}'",
      "kubectl -n <ns> top pod <pod> --containers",
      "kubectl -n <ns> get pod <pod> -o jsonpath='{.spec.containers[*].resources}'"]),
    (re.compile(r"FailedScheduling|Unschedulable|Insufficient (cpu|memory)|node\(s\) had (taint|untolerated)", re.I),
     "Scheduling failure", "The scheduler found no node satisfying requests, taints, affinity or volume constraints.",
     ["kubectl -n <ns> describe pod <pod>   # Events: FailedScheduling",
      "kubectl describe nodes | grep -A8 -i 'allocated resources'",
      "kubectl get events -A --sort-by=.lastTimestamp | tail -30"]),
    (re.compile(r"\bEvicted\b|DiskPressure|MemoryPressure|ephemeral-storage", re.I), "Eviction / node pressure",
     "The kubelet evicted pods because the node ran short of memory, disk or inodes.",
     ["kubectl get nodes",
      "kubectl describe node <node> | grep -A10 -i conditions",
      "journalctl -u kubelet -n 200 --no-pager"]),
    (re.compile(r"\bforbidden\b|cannot (get|list|watch|create|update|patch|delete) resource|Unauthorized|RBAC", re.I),
     "RBAC / authorization", "The identity making the call lacks permission, or the Role/Binding targets the wrong namespace or ServiceAccount.",
     ["kubectl auth can-i <verb> <resource> --as=system:serviceaccount:<ns>:<serviceaccount> -n <ns>",
      "kubectl get rolebinding,clusterrolebinding -A -o wide | grep <serviceaccount>",
      "kubectl -n <ns> get pod <pod> -o jsonpath='{.spec.serviceAccountName}'"]),
    (re.compile(r"connection refused|no such host|i/o timeout|dial tcp|Name or service not known|no route to host", re.I),
     "Network / DNS", "The target Service has no ready endpoints, the name does not resolve, or a NetworkPolicy/firewall blocks the path.",
     ["kubectl -n <ns> get svc,endpoints <service>",
      "kubectl -n <ns> exec <pod> -- nslookup <service>.<ns>.svc",
      "kubectl get networkpolicy -A"]),
    (re.compile(r"x509|certificate (has expired|signed by unknown|is not valid)|tls: ", re.I), "TLS / certificate",
     "The certificate chain, expiry or hostname does not validate.",
     ["kubectl -n <ns> get certificate,secret | grep -i tls",
      "kubectl -n <ns> get secret <tls-secret> -o jsonpath='{.data.tls\\.crt}' | base64 -d | openssl x509 -noout -dates -subject -issuer",
      "openssl s_client -connect <host>:<port> -servername <host> -showcerts </dev/null"]),
    (re.compile(r"failed calling webhook|admission webhook|webhook .* denied", re.I), "Admission webhook",
     "The API server could not reach the webhook Service, the webhook certificate is invalid, or the webhook rejected the object.",
     ["kubectl get validatingwebhookconfigurations,mutatingwebhookconfigurations",
      "kubectl -n <ns> get svc,endpoints <webhook-service>",
      "kubectl -n <ns> logs deploy/<webhook-deployment> --tail=100"]),
    (re.compile(r"no matches for kind|resource mapping not found|ensure CRDs are installed", re.I), "Missing CRD",
     "A resource was applied before its CustomResourceDefinition was installed (or the apiVersion is wrong).",
     ["kubectl get crd | grep -i <kind>",
      "kubectl api-resources | grep -i <kind>",
      "helm -n <ns> get manifest <release> | grep -n 'kind:'"]),
    (re.compile(r"context deadline exceeded|etcdserver: (request timed out|leader changed)", re.I), "Timeout / deadline exceeded",
     "A dependency was too slow or unreachable. For etcdserver messages, check etcd health and disk latency.",
     ["kubectl get --raw='/readyz?verbose'",
      "journalctl -u etcd -n 200 --no-pager",
      "kubectl -n kube-system logs <etcd-pod> --tail=100"]),
    (re.compile(r"(Readiness|Liveness|Startup) probe failed", re.I), "Probe failure",
     "The kubelet's health check failed; the pod is not Ready or is being restarted.",
     ["kubectl -n <ns> describe pod <pod>   # probe settings and Events",
      "kubectl -n <ns> get pod <pod> -o jsonpath='{.spec.containers[*].readinessProbe}'",
      "kubectl -n <ns> logs <pod> --tail=100"]),
    (re.compile(r"persistentvolumeclaim .* (not found|unbound)|pod has unbound|FailedMount|FailedAttachVolume", re.I), "Storage / PVC",
     "The PVC is Pending/unbound, the StorageClass is missing, or the volume failed to attach or mount.",
     ["kubectl -n <ns> get pvc,pv",
      "kubectl get storageclass",
      "kubectl -n <ns> describe pvc <pvc>"]),
    (re.compile(r"another operation \(install/upgrade/rollback\) is in progress|cannot re-use a name|UPGRADE FAILED|INSTALLATION FAILED", re.I), "Helm release state",
     "A previous Helm operation is stuck (pending-*) or a release of that name already exists.",
     ["helm -n <ns> status <release>",
      "helm -n <ns> history <release>",
      "helm -n <ns> list --all --pending"]),
    (re.compile(r"kubelet|containerd|runc|cgroup|CNI|failed to (create|start) (pod )?sandbox", re.I), "Node runtime (kubelet / containerd / CNI)",
     "The failure is below Kubernetes objects: the container runtime, kubelet or CNI plugin on the node.",
     ["journalctl -u kubelet -n 200 --no-pager",
      "journalctl -u containerd -n 200 --no-pager",
      "crictl ps -a && crictl pods"]),
]


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def extract_frames(text: str) -> list[dict]:
    frames: list[dict] = []
    seen: set[tuple[str, int]] = set()

    def add(path: str, line: str, func: str | None, lang: str) -> None:
        key = (path, int(line))
        if key not in seen and int(line) > 0:
            seen.add(key)
            frames.append({"path": path, "line": int(line), "func": func or "", "lang": lang})

    for raw in text.splitlines():
        for m in PY_FRAME.finditer(raw):
            add(m.group(1), m.group(2), m.group(3), "python")
        for m in JAVA_FRAME.finditer(raw):
            qualified = m.group(1).replace("$", ".").split(".")
            package_dir = "/".join(qualified[:-2]) if len(qualified) > 2 else ""
            path = f"{package_dir}/{m.group(2)}" if package_dir else m.group(2)
            add(path, m.group(3), qualified[-1], "java")
        for m in JS_FRAME.finditer(raw):
            add(m.group(2), m.group(3), m.group(1), "js")
        for m in GO_FRAME.finditer(raw):
            add(m.group(1), m.group(2), None, "go")
        for m in OTHER_FRAME.finditer(raw):
            add(m.group(1), m.group(2), None, "other")
    return frames[:40]


def _suffix_overlap(rel: str, seen_parts: list[str]) -> int:
    """Number of trailing path components shared by an indexed path and a path seen in a log."""
    n = 0
    for a, b in zip(reversed(rel.split("/")), reversed(seen_parts)):
        if a != b:
            break
        n += 1
    return n


def locate_frames(db: "Database", repo_id: int, frames: list[dict]) -> list[dict]:
    """Map frames onto indexed repository files by longest matching path suffix."""
    located: list[dict] = []
    for frame in frames:
        seen_path = _norm(frame["path"])
        base = posixpath.basename(seen_path)
        rows = db.query(
            "SELECT rel_path FROM files WHERE repo_id = ? AND (rel_path = ? OR rel_path LIKE ?)",
            (repo_id, base, f"%/{base}"),
        )
        if not rows:
            continue
        seen_parts = seen_path.split("/")
        scored = sorted(((_suffix_overlap(r["rel_path"], seen_parts), r["rel_path"]) for r in rows), reverse=True)
        best = scored[0][0]
        for score, rel in scored[:2]:
            if score == best:
                located.append({**frame, "rel_path": rel, "ambiguous": len(rows) > 1 and best <= 1})
    return located


def extract_signals(text: str) -> dict:
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    error_lines: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if ERROR_LINE.search(line):
            key = NOISE.sub("#", line)[:160]
            if key not in seen:
                seen.add(key)
                error_lines.append(line.strip()[:300])
        if len(error_lines) >= 12:
            break
    hints = []
    for pattern, label, meaning, commands in HINTS:
        if pattern.search(text):
            hints.append({"label": label, "meaning": meaning, "commands": commands})
    return {"frames": extract_frames(text), "error_lines": error_lines, "hints": hints}


def retrieval_query(question: str, signals: dict) -> str:
    """Build a search query from the report: error lines and frame function names, minus timestamps/hashes and filler words."""
    parts = [question] + signals["error_lines"][:6] + [f["func"] for f in signals["frames"] if f["func"]][:8]
    query = GENERIC_WORDS.sub(" ", NOISE.sub(" ", " ".join(parts)))
    return query[:1500]
