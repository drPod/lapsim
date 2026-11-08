"""OpenAI-compatible client for the vLLM server (Nemotron Nano VL)."""
from __future__ import annotations

import base64
import json
import re
import time

import cv2
import httpx
import numpy as np

from .tools import TOOLS, TOOL_NAMES

OBSERVER_SYSTEM = """/no_think
You are the vision system of a laparoscopic surgery box trainer. The camera looks into a box that contains:
- a GREEN plastic bag of liquid = the gallbladder,
- a BLUE sponge = the liver,
- an ORANGE tube and a CLEAR tube = the bile ducts,
- up to two long BLACK/dark grey sticks = the surgical instruments, entering from the left and right.
Each stick ends in a tip. Decide, for each visible stick tip, what the very end of the tip is touching.
"touching" means the tip is in contact with or pressing into the object (indenting, deforming, resting on it).
A tip that only overlaps the object in the picture but is clearly in front of it or a gap is visible is NOT touching.
Answer only with the JSON object requested."""

OBSERVER_USER = """Describe this frame.
For each instrument ("left" = enters from the left edge, "right" = enters from the right edge) report:
- visible: is the stick visible at all,
- touching: what its tip touches: "gallbladder", "orange_tube", "clear_tube", "liver", or "nothing",
- action: "none", "poking" (tapping/moving against it), "pressing" (holding the tip still against it),
  "pulling" (dragging or lifting it so it deforms or moves), or "grasping".
Also report whether the gallbladder looks deformed or displaced, whether green liquid has leaked out,
your confidence 0-1, and a one-sentence summary."""

OBSERVATION_SCHEMA = {
    "type": "object",
    "properties": {
        "left": {"$ref": "#/$defs/tip"},
        "right": {"$ref": "#/$defs/tip"},
        "gallbladder_deformed": {"type": "boolean"},
        "liquid_leak_visible": {"type": "boolean"},
        "confidence": {"type": "number"},
        "summary": {"type": "string"},
    },
    "required": ["left", "right", "gallbladder_deformed", "liquid_leak_visible", "confidence", "summary"],
    "$defs": {
        "tip": {
            "type": "object",
            "properties": {
                "visible": {"type": "boolean"},
                "touching": {"type": "string",
                             "enum": ["gallbladder", "orange_tube", "clear_tube", "liver", "nothing"]},
                "action": {"type": "string", "enum": ["none", "poking", "pressing", "pulling", "grasping"]},
            },
            "required": ["visible", "touching", "action"],
        }
    },
}

DIRECTOR_SYSTEM = """/no_think
You are the scenario director of a laparoscopic cholecystectomy simulator used to train surgeons.
The trainee operates on a physical box model; you turn what happens into realistic complications in the
simulation. Complications must feel real: bleeding when the gallbladder is handled, bile when a duct is
handled, and things that go wrong on their own. Keep it tense but plausible.

Every tick you get the latest observation of the instruments, the current simulation state and a list of
actions the rules engine already executed this tick. You may call tools from the fixed list:
- narrate with staff_say (realistic short OR lines from Scrub nurse, Anesthesia, Circulator or Attending,
  e.g. anaesthesia calling out falling pressure, the attending telling the trainee to apply pressure),
- show_warning for important alarms,
- escalate_bleed if the trainee is being rough with a bleeding gallbladder,
- mark_controlled ONLY if the state says a complication's control criterion is met,
- no_action if nothing is worth saying.
Do not repeat a line that was said in the last 20 seconds. Call at most 2 tools."""


def encode_jpeg(img: np.ndarray, width: int) -> str:
    h, w = img.shape[:2]
    if w > width:
        img = cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf.tobytes()).decode()


_TOOLCALL_RE = re.compile(r"<TOOLCALL>(.*?)</TOOLCALL>", re.S)


def parse_toolcall_text(text: str) -> list[dict]:
    """Fallback for servers without a tool-call parser: Nemotron emits <TOOLCALL>[{...}]</TOOLCALL>."""
    calls = []
    for m in _TOOLCALL_RE.finditer(text or ""):
        try:
            arr = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for c in arr if isinstance(arr, list) else [arr]:
            args = c.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append({"name": c.get("name"), "arguments": args})
    return calls


class VLM:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.http = httpx.Client(base_url=cfg["base_url"], timeout=cfg.get("timeout_s", 15))
        self.model = cfg["model"]

    def _chat(self, body: dict) -> tuple[dict, float, dict]:
        t0 = time.perf_counter()
        r = self.http.post("/chat/completions", json={"model": self.model, **body})
        r.raise_for_status()
        j = r.json()
        return j["choices"][0]["message"], (time.perf_counter() - t0) * 1000, j.get("usage", {})

    def observe(self, img: np.ndarray, extra_hint: str | None = None) -> dict:
        b64 = encode_jpeg(img, self.cfg.get("image_width", 768))
        text = OBSERVER_USER + (f"\n{extra_hint}" if extra_hint else "")
        msg, ms, usage = self._chat({
            "messages": [
                {"role": "system", "content": OBSERVER_SYSTEM},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": text},
                ]},
            ],
            "max_tokens": self.cfg.get("observer_max_tokens", 300),
            "temperature": 0.0,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "observation", "schema": OBSERVATION_SCHEMA}},
        })
        content = (msg.get("content") or "").strip()
        content = content.split("</think>")[-1].strip()
        try:
            obs = json.loads(content[content.index("{"): content.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError):
            obs = {"error": "unparseable", "raw": content[:400]}
        obs["_raw"] = content[:600]
        obs["_ms"] = round(ms)
        obs["_tokens"] = usage.get("completion_tokens")
        return obs

    def direct(self, context: dict) -> tuple[list[dict], dict]:
        # JSON mode instead of the OpenAI `tools` field: works on a vLLM started without a tool-call parser.
        system = (DIRECTOR_SYSTEM + "\n\nTool catalog (JSON schema):\n" + json.dumps(TOOLS, separators=(",", ":"))
                  + '\n\nReply with only a JSON object: {"tool_calls": [{"name": <tool name>, "arguments": {...}}]}')
        msg, ms, usage = self._chat({
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(context, separators=(",", ":"))},
            ],
            "max_tokens": self.cfg.get("director_max_tokens", 400),
            "temperature": 0.4,
            "response_format": {"type": "json_object"},
        })
        content = (msg.get("content") or "").split("</think>")[-1].strip()
        calls = []
        try:
            raw = json.loads(content[content.index("{"): content.rindex("}") + 1]).get("tool_calls") or []
        except (ValueError, json.JSONDecodeError, AttributeError):
            raw = []
        for c in raw if isinstance(raw, list) else []:
            if not isinstance(c, dict):
                continue
            args = c.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append({"name": c.get("name"), "arguments": args})
        if not calls:
            calls = parse_toolcall_text(content)
        calls = [c for c in calls if c["name"] in TOOL_NAMES][:2]
        return calls, {"ms": round(ms), "tokens": usage.get("completion_tokens"), "text": content[:600]}
