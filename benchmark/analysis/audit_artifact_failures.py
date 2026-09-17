"""Read-only forensic checks for the September 15 artifact failures.

This does not import or execute generated programs, invoke a model, access the
network, or read configuration/credentials. Expected answers are read only for
post-run diagnosis; they must never be injected into an agent benchmark run.
"""
from __future__ import annotations

import argparse
from collections import Counter
import difflib
import json
from pathlib import Path
import re


TASKS = (
    "video-processing", "protein-assembly", "path-tracing",
    "build-pov-ray", "distribution-search",
)


def read_events(path: Path) -> list[dict]:
    events = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        event = json.loads(line)
        assert event["sequence"] == line_number, (path, line_number)
        event["audit_line"] = line_number
        events.append(event)
    return events


def translate_dna(text: str) -> str:
    """Decode plain text with the standard genetic code; no artifact execution."""
    assert set(text) <= set("ACGT") and len(text) % 3 == 0
    bases = "TCAG"
    letters = "FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG"
    code = {
        a + b + c: letters[i * 16 + j * 4 + k]
        for i, a in enumerate(bases)
        for j, b in enumerate(bases)
        for k, c in enumerate(bases)
    }
    return "".join(code[text[i:i + 3]] for i in range(0, len(text), 3))


def audit(root: Path) -> dict:
    report = {"run": str(root.resolve()), "mode": "read_only_static_analysis", "tasks": {}}
    for task in TASKS:
        trial = next(root.glob(task + "__*"))
        journal = next((trial / "agent").glob("session-*.jsonl"))
        events = read_events(journal)
        terminal = next(event for event in events if event["type"] == "turn_completed")
        final = terminal["payload"]
        requests = [event for event in events if event["type"] == "tool_requested"]
        output = {
            "journal": str(journal.resolve()),
            "terminal_line": terminal["audit_line"],
            "status": final["status"],
            "completion_report": final["completion_report"],
            "statistics": final["statistics"],
            "usage": final["usage"],
            "tool_counts": dict(Counter(event["payload"]["name"] for event in requests)),
            "external_payload_events": [event["audit_line"] for event in events if "payload_ref" in event],
        }
        verifier = (trial / "verifier" / "test-stdout.txt").read_text(encoding="utf-8")
        output["official_failure_lines"] = [
            {"line": number, "text": line.strip()}
            for number, line in enumerate(verifier.splitlines(), 1)
            if line.startswith("E       ") or "failed, " in line
        ]
        if task == "protein-assembly":
            write = next(event for event in requests
                         if event["payload"]["name"] == "write_file"
                         and event["payload"]["arguments"].get("path") == "/app/gblock.txt")
            dna = write["payload"]["arguments"]["content"]
            protein = translate_dna(dna)
            references = dict(re.findall(r'(flag|donor|dhfr|acceptor|snap)_aa = "([A-Z]+)"', verifier))
            components = protein.split("GSGSGSGSGS")
            assert len(components) == len(references) == 5
            mismatches = []
            for (name, reference), actual in zip(references.items(), components):
                differences = [
                    {"operation": tag, "reference_span": [i, j], "actual_span": [k, end]}
                    for tag, i, j, k, end in difflib.SequenceMatcher(
                        None, reference, actual, autojunk=False
                    ).get_opcodes() if tag != "equal"
                ]
                mismatches.append({"component": name, "expected_length": len(reference),
                                   "actual_length": len(actual),
                                   "reference_position_in_artifact": protein.find(reference),
                                   "differences": differences})
            assert all(item["reference_position_in_artifact"] >= 0
                       for item in mismatches if item["component"] != "donor")
            assert components[1] == references["donor"][21:]
            output["static_artifact_check"] = {
                "source_write_line": write["audit_line"], "dna_length": len(dna),
                "protein_length": len(protein), "components": mismatches,
                "assertion": "Only the donor differs: its first 21 reference residues are absent.",
            }
        elif task == "path-tracing":
            write = next(event for event in requests
                         if event["payload"]["name"] == "write_file"
                         and event["payload"]["arguments"].get("path") == "image.c")
            source = write["payload"]["arguments"]["content"]
            assert 'system("cd /tmp&&/app/orig' in source
            assert '"/tmp/image" ".ppm"' in source
            assert "image.ppm" not in source
            output["static_artifact_check"] = {
                "source_write_line": write["audit_line"], "source_characters": len(source),
                "external_renderer": "/app/orig", "requires_system_shell": True,
                "literal_filename_check_passes": True,
                "assertion": "Source delegates rendering to an external executable; substring absence does not prove no input dependency.",
            }
        elif task == "video-processing":
            observed = next(event for event in requests if event["audit_line"] == 89)
            assert "49-65" in " ".join(observed["payload"]["arguments"]["evidence"])
            result = next(event for event in events if event["audit_line"] == 214)
            evidence = result["payload"].get("evidence", result["payload"].get("verification", result["payload"]))
            measured = json.loads(evidence["diagnostic"].splitlines()[-1])
            assert (measured["takeoff"], measured["landing"]) == (94, 109)
            assert measured["ordered"] and measured["takeoff"] > 65
            output["static_artifact_check"] = {
                "scene_observation_line": 89, "end_to_end_result_line": 214,
                "scene_observation_interval": [49, 65], "result": measured,
                "assertion": "The accepted ordered/bounded result contradicts the agent's own earlier jump interval observation.",
            }
        report["tasks"][task] = output
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    serialized = json.dumps(audit(args.run), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
        print(f"Static artifact checks passed; report written to {args.output}")
    else:
        print(serialized)
