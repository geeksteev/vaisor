#!/usr/bin/env python3
"""Parse Claude's text output for the white-claw wrapper scripts.

  wc-parse.py review MAX   < result.txt  -> {"comment": str, "suggestions": [{title, body}]}
  wc-parse.py plan   MAX   < result.txt  -> [{title, body}]
  wc-parse.py triage MAX   < result.txt  -> [{number, decision: approve|close, reason}]
  wc-parse.py roadmap MAX  < ROADMAP.md  -> [{title, body}]  one per "## Goal: ..." section, in order

Anything malformed is dropped rather than guessed at.
"""
import json
import re
import sys


def clean_items(items, limit):
    out = []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title", "")).strip()
        body = str(it.get("body", "")).strip()
        if not title or len(title) > 200:
            continue
        out.append({"title": title, "body": body[:20000]})
        if len(out) >= limit:
            break
    return out


def first_json_array(text):
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []


def main():
    mode, limit = sys.argv[1], int(sys.argv[2])
    text = sys.stdin.read()

    if mode == "review":
        m = re.search(r"<suggestions>\s*(.*?)\s*</suggestions>", text, re.S)
        suggestions = clean_items(first_json_array(m.group(1)), limit) if m else []
        comment = re.sub(r"\s*<suggestions>.*?</suggestions>\s*", "\n\n", text, flags=re.S).strip()
        json.dump({"comment": comment, "suggestions": suggestions}, sys.stdout)
    elif mode == "plan":
        json.dump(clean_items(first_json_array(text), limit), sys.stdout)
    elif mode == "triage":
        out = []
        for it in first_json_array(text) if isinstance(first_json_array(text), list) else []:
            if not isinstance(it, dict):
                continue
            try:
                number = int(it.get("number"))
            except (TypeError, ValueError):
                continue
            decision = str(it.get("decision", "")).strip().lower()
            if decision not in ("approve", "close"):
                continue
            out.append({"number": number, "decision": decision,
                        "reason": str(it.get("reason", "")).strip()[:2000]})
            if len(out) >= limit:
                break
        json.dump(out, sys.stdout)
    elif mode == "roadmap":
        goals = []
        for m in re.finditer(r"^##\s+(Goal:.*?)\s*$\n(.*?)(?=^##\s|\Z)", text, re.M | re.S):
            goals.append({"title": m.group(1).strip(), "body": m.group(2).strip()})
        json.dump(clean_items(goals, limit), sys.stdout)
    else:
        sys.exit(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
