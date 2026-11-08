"""The fixed tool list the director can call. Nothing outside this list is ever executed."""
from __future__ import annotations


def _fn(name, desc, props=None, required=None):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": required or []},
        },
    }


_sev = {"type": "number", "minimum": 0, "maximum": 1, "description": "0..1"}
_reason = {"type": "string", "description": "one short sentence for the debrief log"}

TOOLS = [
    _fn("start_bleed", "Start a bleed where an instrument touched the gallbladder (or liver).",
        {"severity": _sev, "site": {"type": "string", "enum": ["gallbladder", "liver"]},
         "pulsatile": {"type": "boolean"}, "reason": _reason}, ["severity", "reason"]),
    _fn("escalate_bleed", "Make the active bleed worse (trainee keeps poking, pulling or tearing).",
        {"severity": _sev, "reason": _reason}, ["severity", "reason"]),
    _fn("start_bile_leak", "Bile leak after an instrument touched a duct (tube).",
        {"severity": _sev, "duct": {"type": "string", "enum": ["cystic", "common_bile"]}, "reason": _reason},
        ["severity", "reason"]),
    _fn("duct_injury", "Escalate a bile leak into a duct injury (repeated or rough handling of a duct).",
        {"duct": {"type": "string", "enum": ["cystic", "common_bile"]}, "reason": _reason}, ["reason"]),
    _fn("insufflation_loss", "Pneumoperitoneum is lost: the working space collapses.", {"reason": _reason}, ["reason"]),
    _fn("bp_drop", "Anaesthesia event: sudden hypotension unrelated to the trainee.", {"reason": _reason}, ["reason"]),
    _fn("camera_fog", "The laparoscope lens fogs up.", {"reason": _reason}, ["reason"]),
    _fn("smoke", "Surgical smoke fills the view.", {"intensity": _sev, "reason": _reason}, ["reason"]),
    _fn("mark_controlled", "Declare an active complication handled (e.g. steady pressure held long enough).",
        {"complication": {"type": "string", "enum": ["bleed", "bile_leak", "insufflation_loss", "bp_drop",
                                                      "camera_fog", "smoke"]}, "reason": _reason},
        ["complication", "reason"]),
    _fn("staff_say", "An OR team member says one short realistic line.",
        {"speaker": {"type": "string", "enum": ["Scrub nurse", "Anesthesia", "Circulator", "Attending"]},
         "text": {"type": "string", "maxLength": 160}}, ["speaker", "text"]),
    _fn("show_warning", "Banner on the laparoscope monitor.",
        {"text": {"type": "string", "maxLength": 80}, "level": {"type": "string", "enum": ["info", "warning", "critical"]}},
        ["text"]),
    _fn("no_action", "Nothing needs to happen this tick.", {"reason": _reason}),
]

TOOL_NAMES = [t["function"]["name"] for t in TOOLS]
