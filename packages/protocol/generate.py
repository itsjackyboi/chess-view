#!/usr/bin/env python3
"""Emit ``schema.json`` from the pydantic models.

The TypeScript types the mobile client imports are generated from that schema by
``npm run generate`` in this package, so the protocol is defined exactly once. CI
re-runs generation and fails on a diff, which is what stops the two ends drifting.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from pydantic import TypeAdapter

from chessview_protocol.messages import (  # noqa: E402
    PROTOCOL_VERSION,
    ClientMessage,
    FrameHeader,
    ServerMessage,
)

OUT = Path(__file__).parent / "schema.json"


def build() -> dict:
    defs: dict = {}
    props: dict = {}
    for name, adapter in (
        ("ClientMessage", TypeAdapter(ClientMessage)),
        ("ServerMessage", TypeAdapter(ServerMessage)),
        ("FrameHeader", TypeAdapter(FrameHeader)),
    ):
        schema = adapter.json_schema(by_alias=True, ref_template="#/definitions/{model}")
        defs.update(schema.pop("$defs", {}))
        defs[name] = schema
        props[name] = {"$ref": f"#/definitions/{name}"}

    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "ChessViewProtocol",
        "description": f"Generated from chessview_protocol.messages (v{PROTOCOL_VERSION}). Do not edit by hand.",
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
        "definitions": defs,
    }


if __name__ == "__main__":
    OUT.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {OUT}")
