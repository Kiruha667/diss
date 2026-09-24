"""Adapter: SWE-bench Verified, mini-SWE-agent "bash-only" runs -> unified trajectory records.

Source layout (public bucket, unsigned HTTPS; verified 2026-09-23):
    s3://swe-bench-submissions/bash-only/<sub>/trajs/<task>/<task>.traj.json
    s3://swe-bench-submissions/bash-only/<sub>/logs/<task>/report.json
The true prefix of each run is taken from its metadata.yaml (assets.trajs), never guessed.

Label = 1 - report.json["resolved"] (SWE-bench harness, FAIL_TO_PASS + PASS_TO_PASS tests).
report.json comes in two schemas: flat {"resolved": ...} and nested {<task>: {"resolved": ...}}.
per_instance_details.json is NOT used (it is wrong for some runs).

Trajectory formats handled:
* v1.x text: assistant content "THOUGHT: ... ```bash <cmd>```"; tool outputs come back as role=user
  messages starting with <returncode>; other user messages are harness feedback.
* MiniMax-M2 (v1.17.0): actions in <bash_code> tags; reasoning only in extra.response.
* v2.x chat tool-calls: reasoning in reasoning_content | reasoning | reasoning_details; commands in
  tool_calls[].function.arguments; outputs as role=tool; final role=exit carries the patch.
The submitted patch (role=exit, or the trailing message after the final submit in v1) is dropped:
it is the agent's final answer and would leak the outcome.
"""
from __future__ import annotations

import csv
import json
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterator

import pandas as pd
import requests
import yaml

from src.common import get_logger, write_jsonl_gz

log = get_logger("swebench_bashonly")

SOURCE = "swebench_bashonly"
S3_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
SUBMIT_SENTINELS = ("MICRO_SWE_AGENT_FINAL_OUTPUT", "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")

# --------------------------------------------------------------------------------------- download


def _session(workers: int) -> requests.Session:
    s = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=workers, pool_maxsize=workers)
    s.mount("https://", adapter)
    return s


def _get(session: requests.Session, url: str, params: dict | None = None, timeout: int = 120,
         retries: int = 6) -> requests.Response | None:
    """GET with exponential backoff. Returns None on 404/403 (object absent)."""
    delay = 2.0
    for attempt in range(retries):
        try:
            r = session.get(url, params=params, timeout=timeout)
            if r.status_code in (403, 404):
                return None
            if r.status_code == 200:
                return r
            log.warning("GET %s -> HTTP %s (attempt %d)", url, r.status_code, attempt + 1)
        except requests.RequestException as e:
            log.warning("GET %s -> %s (attempt %d)", url, e, attempt + 1)
        time.sleep(delay)
        delay = min(delay * 2, 60)
    raise RuntimeError(f"GET failed after {retries} attempts: {url}")


def fetch_metadata(cfg: dict, sub: str, raw_dir: Path, session: requests.Session) -> dict:
    path = raw_dir / sub / "metadata.yaml"
    if not path.exists():
        r = _get(session, f"{cfg['metadata_base']}/{sub}/metadata.yaml")
        if r is None:
            raise FileNotFoundError(f"no metadata.yaml for {sub} in SWE-bench/experiments")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def run_prefix(meta: dict, cfg: dict, sub: str) -> str:
    """S3 key prefix of a run (without trailing slash), from metadata.yaml assets.trajs."""
    trajs = ((meta.get("assets") or {}).get("trajs") or "").strip()
    m = re.match(r"s3://[^/]+/(.+?)/trajs/?$", trajs)
    if m:
        return m.group(1)
    log.warning("%s: metadata.yaml has no S3 assets.trajs (%r); falling back to %s/%s", sub, trajs,
                cfg["s3_prefix"], sub)
    return f"{cfg['s3_prefix']}/{sub}"


def list_keys(cfg: dict, prefix: str, session: requests.Session) -> list[tuple[str, int]]:
    """All (key, size) under an S3 prefix (unsigned ListObjectsV2 with pagination)."""
    out, token = [], None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        r = _get(session, cfg["s3_base"] + "/", params=params)
        if r is None:
            raise RuntimeError(f"S3 listing refused for prefix {prefix}")
        root = ET.fromstring(r.content)
        for c in root.findall(f"{S3_NS}Contents"):
            out.append((c.find(f"{S3_NS}Key").text, int(c.find(f"{S3_NS}Size").text)))
        if root.findtext(f"{S3_NS}IsTruncated") == "true":
            token = root.findtext(f"{S3_NS}NextContinuationToken")
        else:
            return out


def _download_file(session: requests.Session, url: str, dest: Path) -> tuple[bool, int]:
    if dest.exists() and dest.stat().st_size > 0:
        return True, dest.stat().st_size
    r = _get(session, url)
    if r is None:
        return False, 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(r.content)
    tmp.replace(dest)
    return True, len(r.content)


def download(cfg: dict, raw_dir: Path, workers: int = 16, tasks: list[str] | None = None,
             runs: list[str] | None = None) -> pd.DataFrame:
    """Download trajectories + report.json of the configured runs into raw_dir/<sub>/. Resumable."""
    raw_dir = Path(raw_dir)
    session = _session(workers)
    rows = []
    for run in cfg["runs"]:
        sub = run["sub"]
        if runs and sub not in runs:
            continue
        meta = fetch_metadata(cfg, sub, raw_dir, session)
        prefix = run_prefix(meta, cfg, sub)
        keys = list_keys(cfg, f"{prefix}/trajs/", session)
        pat = re.compile(rf"^{re.escape(prefix)}/trajs/([^/]+)/\1\.traj\.json$")
        found = {m.group(1): k for k, _ in keys if (m := pat.match(k))}
        wanted = sorted(found) if tasks is None else [t for t in tasks if t in found]
        log.info("%s: prefix=%s, %d trajs listed, downloading %d", sub, prefix, len(found), len(wanted))
        jobs = {}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for t in wanted:
                jobs[ex.submit(_download_file, session, f"{cfg['s3_base']}/{found[t]}",
                               raw_dir / sub / "trajs" / f"{t}.traj.json")] = (t, "traj")
                jobs[ex.submit(_download_file, session,
                               f"{cfg['s3_base']}/{prefix}/logs/{t}/report.json",
                               raw_dir / sub / "reports" / f"{t}.report.json")] = (t, "report")
            res: dict[str, dict] = {t: {"sub": sub, "task_id": t} for t in wanted}
            for i, fut in enumerate(as_completed(jobs), 1):
                t, kind = jobs[fut]
                ok, size = fut.result()
                res[t][f"{kind}_ok"] = ok
                res[t][f"{kind}_bytes"] = size
                if i % 500 == 0:
                    log.info("%s: %d/%d files", sub, i, len(jobs))
        man = pd.DataFrame(list(res.values()))
        (raw_dir / sub).mkdir(parents=True, exist_ok=True)
        man.to_csv(raw_dir / sub / "manifest.csv", index=False)
        n_rep = int(man["report_ok"].sum()) if len(man) else 0
        log.info("%s: done, %d trajs, %d reports", sub, len(man), n_rep)
        rows.append(man)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


# ---------------------------------------------------------------------------------------- parsing


def read_label(report_path: Path, task_id: str) -> int | None:
    """1 = failure, 0 = success, None = no report. Handles flat and nested report.json."""
    if not report_path.exists():
        return None
    rep = json.loads(report_path.read_text(encoding="utf-8"))
    if "resolved" in rep:
        resolved = rep["resolved"]
    elif task_id in rep and isinstance(rep[task_id], dict):
        resolved = rep[task_id].get("resolved", False)
    elif len(rep) == 1 and isinstance(next(iter(rep.values())), dict):
        resolved = next(iter(rep.values())).get("resolved", False)
    else:
        raise ValueError(f"unrecognised report.json schema: {report_path}")
    return 0 if bool(resolved) else 1


def _text_of(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                parts.append(p.get("text") or "")
            elif isinstance(p, str):
                parts.append(p)
        return "\n".join(x for x in parts if x)
    return str(content)


_PR_RE = re.compile(r"<pr_description>(.*?)</pr_description>", re.S)
_PR_PREFIX_RE = re.compile(r"^\s*Consider the following PR description:\s*", re.S)


def extract_task_text(first_user_text: str) -> str | None:
    m = _PR_RE.search(first_user_text)
    if not m:
        return None
    t = _PR_PREFIX_RE.sub("", m.group(1))
    t = "\n".join(line.rstrip() for line in t.strip().splitlines())
    return re.sub(r"\n{3,}", "\n\n", t)


# Only bash fences are actions in mini-swe-agent v1 (```python etc. inside reasoning are not commands).
_FENCE_RE = re.compile(r"```(?:bash|sh)[ \t]*\n(.*?)```", re.S)
_BASH_CODE_RE = re.compile(r"<bash_code>(.*?)</bash_code>", re.S)
_RC_RE = re.compile(r"<returncode>\s*(-?\d+)\s*</returncode>")
_WRAPPER_TAGS_RE = re.compile(r"</?(?:output|warning|output_head|output_tail|elided_chars|output_full)>")


def clean_tool_output(text: str) -> str:
    """Strip mini-swe-agent XML wrappers; keep the inner text; keep a non-zero return code."""
    rc = _RC_RE.search(text)
    body = _RC_RE.sub("", text)
    body = _WRAPPER_TAGS_RE.sub("", body).strip("\n")
    if rc and rc.group(1) != "0":
        body = f"returncode: {rc.group(1)}\n{body}"
    return body


def _reasoning_of(m: dict) -> tuple[str, str]:
    """(reasoning text, source). One source only — the fields often duplicate each other."""
    for key in ("reasoning_content", "reasoning"):
        v = m.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip(), key
    det = m.get("reasoning_details")
    if isinstance(det, list):
        t = "\n".join((d.get("text") or d.get("summary") or "") for d in det if isinstance(d, dict)).strip()
        if t:
            return t, "reasoning_details"
    # v1 runs of some providers keep the reasoning only in the raw provider response.
    try:
        msg = m["extra"]["response"]["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return "", ""
    if isinstance(msg, dict):
        for key in ("reasoning_content", "reasoning"):
            v = msg.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip(), f"extra.{key}"
    return "", ""


def _commands_from_tool_calls(tool_calls) -> list[str]:
    cmds = []
    for tc in tool_calls or []:
        fn = (tc or {}).get("function") or {}
        args = fn.get("arguments")
        cmd = None
        if isinstance(args, str):
            try:
                a = json.loads(args)
                cmd = a.get("command") if isinstance(a, dict) else None
            except json.JSONDecodeError:
                cmd = args
        elif isinstance(args, dict):
            cmd = args.get("command")
        if cmd is None and args is not None:
            cmd = json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args
        if cmd:
            cmds.append(cmd.strip())
    return cmds


def _assistant_step(m: dict) -> tuple[str, list[str], str]:
    """Normalised assistant text (reasoning + content + fenced commands), commands, reasoning source."""
    reasoning, rsrc = _reasoning_of(m)
    content = _text_of(m.get("content")).strip()
    # MiniMax-M2 style <bash_code> actions -> fenced blocks, so all formats look the same downstream.
    content = _BASH_CODE_RE.sub(lambda mm: f"```bash\n{mm.group(1).strip()}\n```", content)
    tc_cmds = _commands_from_tool_calls(m.get("tool_calls"))
    if tc_cmds:
        cmds = tc_cmds
        blocks = "\n\n".join(f"```bash\n{c}\n```" for c in tc_cmds)
        text = "\n\n".join(x for x in (reasoning, content, blocks) if x)
    else:
        # v1 executes a command only when the message has exactly ONE bash block; otherwise the
        # harness answers with a format error and nothing runs -> no tool call.
        blocks = [c.strip() for c in _FENCE_RE.findall(content) if c.strip()]
        cmds = blocks if len(blocks) == 1 else []
        text = "\n\n".join(x for x in (reasoning, content) if x)
    return text, cmds, rsrc


def _is_responses_api(d: dict) -> bool:
    msgs = d.get("messages") or []
    return any(isinstance(m, dict) and (m.get("object") == "response" or m.get("type") in
               ("function_call", "function_call_output")) for m in msgs[:10])


def parse_trajectory(d: dict, sub: str, task_id: str, run_cfg: dict, meta_yaml: dict,
                     infra_statuses: list[str]) -> tuple[dict | None, str | None]:
    """Parse one .traj.json dict. Returns (record_without_label, exclusion_reason)."""
    info = d.get("info") or {}
    exit_status = str(info.get("exit_status") or "")
    if _is_responses_api(d):
        return None, "unsupported_format:responses_api"
    msgs = d.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return None, "unsupported_format:no_messages"
    for s in infra_statuses:
        if s and s in exit_status:
            return None, f"infra_error:{exit_status}"

    # the task statement is in the first user message
    first_user = next((i for i, m in enumerate(msgs) if m.get("role") == "user"), None)
    if first_user is None:
        return None, "no_task_text"
    task_text = extract_task_text(_text_of(msgs[first_user].get("content")))
    if not task_text:
        return None, "no_task_text"

    body = [m for m in msgs[first_user + 1:] if m.get("role") != "exit"]
    # After an accepted submission the harness appends the patch (v1: as a trailing user message).
    # Drop everything after the last assistant message of a submitted run: it is the final answer.
    if exit_status == "Submitted":
        last_a = max((i for i, m in enumerate(body) if m.get("role") == "assistant"), default=None)
        if last_a is not None:
            body = body[: last_a + 1]

    steps, step_idx, rsrcs, n_assistant = [], -1, set(), 0
    for m in body:
        role = m.get("role")
        if role == "assistant":
            text, cmds, rsrc = _assistant_step(m)
            step_idx += 1
            n_assistant += 1
            if rsrc:
                rsrcs.add(rsrc)
            steps.append({"role": "assistant", "text": text, "step_idx": step_idx, "tool_calls": cmds})
        elif role == "tool":
            steps.append({"role": "tool", "text": clean_tool_output(_text_of(m.get("content"))),
                          "step_idx": max(step_idx, 0), "tool_calls": []})
        elif role == "user":
            t = _text_of(m.get("content"))
            if t.lstrip().startswith("<returncode>"):
                steps.append({"role": "tool", "text": clean_tool_output(t), "step_idx": max(step_idx, 0),
                              "tool_calls": []})
            else:
                steps.append({"role": "user", "text": t.strip(), "step_idx": max(step_idx, 0), "tool_calls": []})
        # system / other roles are ignored
    if n_assistant == 0:
        return None, "no_agent_steps"

    version = re.search(r"mini-v(\d+\.\d+\.\d+)", sub)
    tags = meta_yaml.get("tags") or {}
    model = tags.get("model")
    model = model[0] if isinstance(model, list) and model else (model or sub)
    effort = tags.get("reasoning_effort")
    model_id = f"{model}@{effort}" if effort else str(model)
    rec = {
        "run_id": f"{sub}/{task_id}",
        "task_id": task_id,
        "agent_id": f"mini-swe-agent-{version.group(1) if version else 'unknown'}",
        "model_id": model_id,
        "model_family": run_cfg.get("family", "unknown"),
        "source": SOURCE,
        "label": None,
        "task_text": task_text,
        "steps": steps,
        "meta": {
            "sub": sub,
            "exit_status": exit_status,
            "mini_version": info.get("mini_version"),
            "trajectory_format": d.get("trajectory_format"),
            "n_messages": len(msgs),
            "n_assistant": n_assistant,
            "reasoning_sources": sorted(rsrcs),
        },
    }
    return rec, None


def iter_records(cfg: dict, raw_dir: Path) -> Iterator[tuple[dict | None, dict | None]]:
    """Yield (record, None) for kept trajectories and (None, exclusion_row) for excluded ones."""
    raw_dir = Path(raw_dir)
    infra = list(cfg.get("infra_exit_statuses") or [])
    for run in cfg["runs"]:
        sub = run["sub"]
        tdir = raw_dir / sub / "trajs"
        if not tdir.exists():
            log.warning("%s: no raw data at %s — skipped", sub, tdir)
            continue
        meta_path = raw_dir / sub / "metadata.yaml"
        meta_yaml = yaml.safe_load(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        for p in sorted(tdir.glob("*.traj.json")):
            task_id = p.name[: -len(".traj.json")]
            run_id = f"{sub}/{task_id}"
            label = read_label(raw_dir / sub / "reports" / f"{task_id}.report.json", task_id)
            excl = {"run_id": run_id, "task_id": task_id, "label": "" if label is None else label,
                    "stage": "E0", "reason": ""}
            if label is None:
                yield None, {**excl, "reason": "missing_report"}
                continue
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                yield None, {**excl, "reason": f"json_error:{type(e).__name__}"}
                continue
            rec, reason = parse_trajectory(d, sub, task_id, run, meta_yaml, infra)
            del d
            if reason:
                yield None, {**excl, "reason": reason}
                continue
            rec["label"] = label
            yield rec, None


def build(cfg: dict, raw_dir: Path, out_dir: Path) -> tuple[int, int]:
    """Write <out_dir>/trajectories.jsonl.gz and <out_dir>/excluded.csv. Returns (n_kept, n_excluded)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    excluded = []

    def kept():
        for rec, excl in iter_records(cfg, raw_dir):
            if excl is not None:
                excluded.append(excl)
            else:
                yield rec

    n = write_jsonl_gz(out_dir / "trajectories.jsonl.gz", kept())
    with open(out_dir / "excluded.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["run_id", "task_id", "label", "stage", "reason"])
        w.writeheader()
        w.writerows(excluded)
    log.info("built %d trajectories, %d excluded -> %s", n, len(excluded), out_dir)
    return n, len(excluded)


def load_cfg(path: Path | None = None) -> dict:
    from src.common import CONFIG

    path = path or CONFIG / "datasets.yaml"
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))[SOURCE]
