"""Rules engine: fuses CV + VLM contact, owns complication state, enforces the trainee rules
("bag contact -> bleed", "tube contact -> bile"), escalation, control-by-pressure, random events,
cooldowns, and turns every executed tool call into sink events."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .tools import TOOL_NAMES
from .vitals import Vitals

TUBES = ("orange_tube", "clear_tube")
DUCT_OF = {"orange_tube": "cystic", "clear_tube": "common_bile"}


@dataclass
class Complication:
    kind: str  # bleed | bile_leak | insufflation_loss | bp_drop | camera_fog | smoke
    started: float
    severity: float = 0.5
    origin: tuple[float, float] | None = None
    site: str = ""
    hold_s: float = 0.0  # accumulated steady pressure / idle time toward control
    contacts: int = 0  # separate contact episodes since start
    injury: bool = False  # bile leak escalated to duct injury
    pulsatile: bool = False
    expires: float | None = None  # auto-resolve time for non-user events
    pending: float = 0.0  # escalation accumulated but not yet announced

    def view(self, now: float) -> dict:
        d = {"kind": self.kind, "age_s": round(now - self.started, 1), "severity": round(self.severity, 2)}
        if self.kind in ("bleed", "bile_leak"):
            d.update(site=self.site, contacts=self.contacts, hold_s=round(self.hold_s, 1))
        if self.injury:
            d["duct_injury"] = True
        if self.expires:
            d["resolves_in_s"] = round(max(0.0, self.expires - now), 1)
        return d


@dataclass
class TipTrack:
    pos: tuple[float, float] | None = None
    prev: tuple[float, float] | None = None
    on: str = "nothing"
    action: str = "none"
    since: float = 0.0  # time this contact began

    @property
    def motion(self) -> float:
        if self.pos is None or self.prev is None:
            return 0.0
        return math.dist(self.pos, self.prev)


@dataclass
class Engine:
    cfg: dict
    rng: random.Random = field(default_factory=random.Random)

    def __post_init__(self):
        self.p = self.cfg["preset"]
        self.cool = self.cfg["cooldowns"]
        self.active: dict[str, Complication] = {}
        self.last_call: dict[str, float] = {}
        self.tips = {"left": TipTrack(), "right": TipTrack()}
        self.vitals = Vitals()
        self.t0: float | None = None
        self.last_random = -1e9
        self.recent_says: list[tuple[float, str]] = []
        self.bag_prev: tuple[float, float] | None = None
        self.history: list[dict] = []  # executed calls, for director context

    # ------------------------------------------------------------------ fusion
    def fuse(self, now: float, cv: dict | None, obs: dict | None) -> dict:
        """Per-tip contact decision. Returns {left: {...}, right: {...}, bag_shift}"""
        mode = self.cfg["contact"]["fusion"]
        cv_tips = {}
        if cv:
            for t in cv.get("tips", []):
                # boxtracker frame: sticks enter from the edges; "side" is the entry edge
                cv_tips.setdefault(t["side"], t)
        out = {}
        for side in ("left", "right"):
            ct = cv_tips.get(side)
            cv_on = None
            if ct:
                cv_on = {"bag": "gallbladder", "orange_tube": "orange_tube"}.get(ct["on"], "nothing")
            vo = (obs or {}).get(side) or {}
            vlm_on = vo.get("touching") if "error" not in (obs or {"error": 1}) else None
            if vlm_on == "liver":
                vlm_on = "nothing"  # sponge contact is harmless here
            on = _fuse_one(mode, cv_on, vlm_on)
            out[side] = {"on": on, "cv": cv_on, "vlm": vlm_on, "action": vo.get("action", "none"),
                         "pos": (ct["x"], ct["y"]) if ct else None}
        bag_shift = 0.0
        if cv and cv.get("bag_centroid"):
            c = tuple(cv["bag_centroid"])
            if self.bag_prev is not None:
                bag_shift = math.dist(c, self.bag_prev)
            self.bag_prev = c
        out["bag_shift"] = bag_shift
        out["vlm_pulling"] = any(((obs or {}).get(s) or {}).get("action") == "pulling" for s in ("left", "right"))
        out["vlm_deformed"] = bool((obs or {}).get("gallbladder_deformed"))
        return out

    # ------------------------------------------------------------------ rules
    def update(self, now: float, dt: float, fused: dict) -> list[dict]:
        """Advance state one tick. Returns tool calls the rules require (source=rule/random)."""
        if self.t0 is None:
            self.t0 = now
        calls: list[dict] = []
        p = self.p
        steady_n = self.cfg["contact"]["steady_px"] / 512.0
        pulling = fused["vlm_pulling"] or fused["bag_shift"] > 0.03

        bag_tips, tube_tips, new_bag_contact, new_tube = [], [], False, None
        for side in ("left", "right"):
            f, tr = fused[side], self.tips[side]
            tr.prev, tr.pos = tr.pos, f["pos"]
            was = tr.on
            tr.on, tr.action = f["on"], f["action"]
            if tr.on != was:
                tr.since = now
            if tr.on == "gallbladder":
                bag_tips.append(side)
                if was != "gallbladder":
                    new_bag_contact = True
            elif tr.on in TUBES:
                tube_tips.append(side)
                if was not in TUBES:
                    new_tube = tr.on

        def steady(side):
            tr = self.tips[side]
            return tr.motion <= steady_n and tr.action in ("pressing", "grasping", "none")

        # --- gallbladder contact -> bleed / escalate / control
        bleed = self.active.get("bleed")
        if bag_tips and bleed is None:
            side = bag_tips[0]
            origin = self.tips[side].pos or (0.5, 0.45)
            sev = p["bleed_start"] + (p["pull_step"] if pulling else 0.0)
            calls.append(_call("start_bleed", "rule", severity=min(sev, 1.0), site="gallbladder",
                               pulsatile=sev >= 0.7, origin=origin,
                               reason=f"{side} tip touched the gallbladder" + (" while pulling" if pulling else "")))
        elif bleed is not None and bag_tips:
            near = [s for s in bag_tips if self._near_origin(s, bleed)]
            holding = [s for s in near if steady(s)] and not pulling
            if holding:
                bleed.hold_s += dt
                if bleed.hold_s >= p["hold_s"]:
                    calls.append(_call("mark_controlled", "rule", complication="bleed",
                                       reason=f"steady pressure held {bleed.hold_s:.1f}s on the bleeding point"))
            else:
                bleed.hold_s = max(0.0, bleed.hold_s - dt)  # decays rather than snapping to zero
                inc = 0.0
                why = []
                if new_bag_contact:
                    bleed.contacts += 1
                    inc += p["bleed_step"]
                    why.append("new contact")
                if pulling:
                    inc += p["pull_step"]
                    why.append("pulling on the gallbladder")
                if not new_bag_contact and not pulling:
                    inc += p["bleed_prolonged_per_s"] * dt
                if inc > 0 and bleed.severity < 1.0:
                    # small prolonged increments accumulate silently; announce in >=0.1 steps
                    bleed.pending += inc
                    if bleed.pending >= 0.1 or why:
                        calls.append(_call("escalate_bleed", "rule",
                                           severity=min(1.0, bleed.severity + bleed.pending),
                                           reason=", ".join(why) or "prolonged rough contact"))
                        bleed.pending = 0.0
        elif bleed is not None:
            bleed.hold_s = max(0.0, bleed.hold_s - dt)

        # --- tube contact -> bile leak / duct injury / clip-and-hold control
        bile = self.active.get("bile_leak")
        if tube_tips and bile is None:
            tube = self.tips[tube_tips[0]].on
            calls.append(_call("start_bile_leak", "rule", severity=p["bile_start"], duct=DUCT_OF[tube],
                               origin=self.tips[tube_tips[0]].pos,
                               reason=f"{tube_tips[0]} tip touched the {tube.replace('_', ' ')}"))
        elif bile is not None and tube_tips:
            if all(steady(s) for s in tube_tips) and not new_tube:
                bile.hold_s += dt
                if bile.hold_s >= p["hold_s"]:
                    calls.append(_call("mark_controlled", "rule", complication="bile_leak",
                                       reason=f"duct held/clipped steadily for {bile.hold_s:.1f}s"))
            else:
                bile.hold_s = max(0.0, bile.hold_s - dt)
                if new_tube:
                    bile.contacts += 1
                    if bile.contacts >= p["duct_injury_after"] and not bile.injury:
                        calls.append(_call("duct_injury", "rule", duct=DUCT_OF[self.tips[tube_tips[0]].on],
                                           reason=f"duct handled again ({bile.contacts} times)"))

        # --- non-user events auto-resolve; insufflation needs instruments idle
        for kind, c in list(self.active.items()):
            if kind == "insufflation_loss":
                if not bag_tips and not tube_tips:
                    c.hold_s += dt
                if c.expires and now >= c.expires and c.hold_s >= 3.0:
                    calls.append(_call("mark_controlled", "rule", complication=kind,
                                       reason="insufflator reset; trainee paused instruments"))
            elif kind in ("bp_drop", "camera_fog", "smoke") and c.expires and now >= c.expires:
                calls.append(_call("mark_controlled", "rule", complication=kind, reason="resolved"))

        # --- random complications (Poisson)
        rcfg = self.cfg["random_events"]
        if rcfg.get("enabled") and now - self.t0 >= rcfg.get("first_after_s", 45) \
                and now - self.last_random >= p["event_cooldown_s"]:
            rate = p["random_per_min"] / 60.0
            if self.rng.random() < 1 - math.exp(-rate * dt):
                kinds = [k for k in rcfg["kinds"] if k not in self.active]
                if kinds:
                    k = self.rng.choice(kinds)
                    args = {"reason": "random event"}
                    if k == "smoke":
                        args["intensity"] = round(self.rng.uniform(0.4, 0.85), 2)
                    calls.append(_call(k, "random", **args))

        # --- vitals
        bleed = self.active.get("bleed")
        rate = bleed.severity * p["bleed_ml_per_s"] if bleed else 0.0
        bpd = 1.0 if "bp_drop" in self.active else 0.0
        self.vitals.step(dt, rate, bpd, "insufflation_loss" in self.active)
        return calls

    def _near_origin(self, side: str, c: Complication) -> bool:
        pos = self.tips[side].pos
        if pos is None or c.origin is None:
            return True  # VLM-only contact: no position, trust it
        return math.dist(pos, c.origin) <= self.cfg["contact"]["hold_radius"]

    # ------------------------------------------------------------------ execution
    def validate(self, now: float, call: dict) -> str | None:
        """Return a rejection reason, or None if the call may execute."""
        name, a = call["name"], call.get("arguments") or {}
        if name not in TOOL_NAMES:
            return "unknown tool"
        last = self.last_call.get(name)
        if last is not None and now - last < self.cool.get(name, 0):
            return "cooldown"
        if name == "start_bleed" and "bleed" in self.active:
            return "bleed already active"
        if name == "escalate_bleed":
            b = self.active.get("bleed")
            if b is None:
                return "no active bleed"
            if float(a.get("severity", 0)) <= b.severity:
                return "not an escalation"
        if name == "start_bile_leak" and "bile_leak" in self.active:
            return "bile leak already active"
        if name == "duct_injury" and ("bile_leak" not in self.active or self.active["bile_leak"].injury):
            return "no bile leak to escalate"
        if name in ("insufflation_loss", "bp_drop", "camera_fog", "smoke") and name in self.active:
            return "already active"
        if name == "mark_controlled":
            k = a.get("complication")
            c = self.active.get(k)
            if c is None:
                return "not active"
            if call.get("source") == "director":
                # the LLM may only confirm what the rules measured
                if k in ("bleed", "bile_leak") and c.hold_s < self.p["hold_s"]:
                    return f"hold {c.hold_s:.1f}s < {self.p['hold_s']}s"
                if c.expires and now < c.expires:
                    return "not resolved yet"
        if name == "staff_say":
            txt = (a.get("text") or "").strip().lower()
            if not txt or any(t == txt and now - ts < 20 for ts, t in self.recent_says):
                return "duplicate line"
        return None

    def execute(self, now: float, call: dict) -> list[dict]:
        """Apply an approved call; return sink events (dicts with 'type')."""
        name, a = call["name"], call.get("arguments") or {}
        self.last_call[name] = now
        self.history.append({"t": round(now - (self.t0 or now), 1), "name": name, "source": call.get("source"),
                             "args": {k: v for k, v in a.items() if k != "reason"}})
        self.history = self.history[-12:]
        p = self.p
        ev: list[dict] = []
        o = a.get("origin")
        origin = {"x": round(o[0], 3), "y": round(o[1], 3)} if o else None
        if name == "start_bleed":
            sev = _clamp(a.get("severity", p["bleed_start"]))
            c = Complication("bleed", now, sev, tuple(o) if o else None, a.get("site", "gallbladder"),
                             pulsatile=bool(a.get("pulsatile", sev >= 0.7)))
            self.active["bleed"] = c
            ev.append(_ev("bleed", intensity=sev, pulsatile=c.pulsatile, origin=origin))
            ev.append(_ev("warning", text="Bleeding — gallbladder bed", level="warning" if sev < 0.7 else "critical"))
        elif name == "escalate_bleed":
            c = self.active["bleed"]
            c.severity = _clamp(a["severity"])
            c.pulsatile = c.pulsatile or c.severity >= 0.7
            ev.append(_ev("bleed", intensity=c.severity, pulsatile=c.pulsatile,
                          origin={"x": round(c.origin[0], 3), "y": round(c.origin[1], 3)} if c.origin else None))
            if c.severity >= 0.7:
                ev.append(_ev("warning", text="Arterial bleeding — cystic artery", level="critical"))
        elif name == "start_bile_leak":
            sev = _clamp(a.get("severity", p["bile_start"]))
            c = Complication("bile_leak", now, sev, tuple(o) if o else None, a.get("duct", "cystic"))
            self.active["bile_leak"] = c
            ev.append(_ev("bile", intensity=sev, origin=origin))
            ev.append(_ev("warning", text="Bile leak", level="warning"))
        elif name == "duct_injury":
            c = self.active["bile_leak"]
            c.injury, c.severity = True, max(c.severity, 0.85)
            ev.append(_ev("bile", intensity=c.severity))
            ev.append(_ev("warning", text=f"{c.site.replace('_', ' ').title()} duct injury", level="critical"))
        elif name == "insufflation_loss":
            self.active[name] = Complication(name, now, expires=now + p["insufflation_s"])
            ev.append(_ev("warning", text="Insufflation pressure lost — 4 mmHg", level="critical"))
            ev.append(_ev("smoke", intensity=0.3, durationMs=0))  # view goes murky while the space collapses
        elif name == "bp_drop":
            self.active[name] = Complication(name, now, expires=now + p["bp_drop_s"])
            ev.append(_ev("warning", text="Hypotension", level="warning"))
        elif name == "camera_fog":
            self.active[name] = Complication(name, now, expires=now + p["fog_s"])
            ev.append(_ev("smoke", intensity=0.6, durationMs=0))
        elif name == "smoke":
            inten = _clamp(a.get("intensity", 0.6))
            self.active[name] = Complication(name, now, inten, expires=now + 10)
            ev.append(_ev("smoke", intensity=inten, durationMs=10000))
        elif name == "mark_controlled":
            k = a["complication"]
            self.active.pop(k, None)
            ev.append({"type": f"{k}_controlled", "reason": a.get("reason", "")})
            if k == "bleed":
                ev.append(_ev("bleed", intensity=0))
                ev.append(_ev("clear", target="warning"))
            elif k == "bile_leak":
                ev.append(_ev("bile", intensity=0))
                ev.append(_ev("clear", target="warning"))
            elif k in ("camera_fog", "insufflation_loss"):
                ev.append(_ev("clear", target="smoke"))
                if k == "insufflation_loss":
                    ev.append(_ev("clear", target="warning"))
            elif k == "bp_drop":
                ev.append(_ev("clear", target="warning"))
        elif name == "staff_say":
            self.recent_says.append((now, (a.get("text") or "").strip().lower()))
            self.recent_says = self.recent_says[-10:]
            ev.append(_ev("say", speaker=a.get("speaker", "Scrub nurse"), text=a["text"][:400]))
        elif name == "show_warning":
            ev.append(_ev("warning", text=a["text"][:120], level=a.get("level", "warning")))
        if name in ("insufflation_loss", "bp_drop", "camera_fog", "smoke"):
            self.last_random = now
        return ev

    def snapshot(self, now: float) -> dict:
        return {
            "t": round(now - (self.t0 or now), 1),
            "difficulty": self.cfg.get("difficulty"),
            "active": [c.view(now) for c in self.active.values()],
            "control_rule": f"bleed/bile controlled after {self.p['hold_s']}s steady pressure at the site",
            "vitals": self.vitals.summary(),
        }


def _fuse_one(mode: str, cv_on: str | None, vlm_on: str | None) -> str:
    if mode == "cv" or vlm_on is None:
        return cv_on or "nothing"
    if mode == "vlm" or cv_on is None:
        return vlm_on or "nothing"
    if mode == "and":
        return cv_on if cv_on == vlm_on else "nothing"
    # "or": either detector; CV wins ties because it localises the tip
    if cv_on != "nothing":
        return cv_on
    return vlm_on


def _clamp(x) -> float:
    try:
        return round(max(0.0, min(1.0, float(x))), 2)
    except (TypeError, ValueError):
        return 0.5


def _call(name: str, source: str, **args) -> dict:
    return {"name": name, "source": source, "arguments": args}


def _ev(type_: str, **kw) -> dict:
    return {"type": type_, **{k: v for k, v in kw.items() if v is not None}}
