from director import config
from director.engine import Engine


def mk(**kw):
    cfg = config.load("config.yaml", [f"{k}={v}" for k, v in kw.items()])
    cfg["random_events"]["enabled"] = False
    return Engine(cfg)


def fused(left="nothing", right="nothing", lpos=(0.4, 0.7), rpos=(0.6, 0.7), action="none", shift=0.0):
    return {"left": {"on": left, "cv": left, "vlm": left, "action": action, "pos": lpos},
            "right": {"on": right, "cv": right, "vlm": right, "action": action, "pos": rpos},
            "bag_shift": shift, "vlm_pulling": action == "pulling", "vlm_deformed": False}


def step(e, now, f):
    out = []
    for c in e.update(now, 1.0, f):
        if e.validate(now, c) is None:
            e.execute(now, c)
            out.append(c["name"])
    return out


def test_touch_starts_bleed_then_pressure_controls():
    e = mk()
    assert step(e, 0, fused()) == []
    assert step(e, 1, fused(left="gallbladder")) == ["start_bleed"]
    assert "bleed" in e.active
    t = 2
    # steady pressure at the same point for hold_s seconds
    names = []
    while "bleed" in e.active and t < 20:
        names += step(e, t, fused(left="gallbladder", action="pressing"))
        t += 1
    assert "mark_controlled" in names
    assert t - 2 == int(e.p["hold_s"])


def test_repeated_poking_and_pulling_escalates():
    e = mk()
    step(e, 0, fused(left="gallbladder"))
    s0 = e.active["bleed"].severity
    step(e, 1, fused())
    assert step(e, 2, fused(left="gallbladder", action="poking")) == ["escalate_bleed"]
    step(e, 5, fused(left="gallbladder", action="pulling", shift=0.05))
    assert e.active["bleed"].severity > s0 + 0.2


def test_tube_contact_bile_then_duct_injury():
    e = mk(difficulty="normal")
    assert step(e, 0, fused(right="orange_tube")) == ["start_bile_leak"]
    step(e, 1, fused())
    step(e, 2, fused(right="orange_tube", action="poking"))
    step(e, 3, fused())
    assert step(e, 4, fused(right="orange_tube", action="poking")) == ["duct_injury"]


def test_vitals_worsen_with_bleed_and_recover():
    e = mk()
    step(e, 0, fused(left="gallbladder", action="poking"))
    for t in range(1, 60):
        step(e, t, fused())
    hr_bleeding, sbp_bleeding = e.vitals.hr, e.vitals.sbp
    assert hr_bleeding > 90 and sbp_bleeding < 115
    e.active.pop("bleed")
    for t in range(60, 200):
        step(e, t, fused())
    assert e.vitals.hr < hr_bleeding - 10


def test_director_cannot_mark_controlled_early():
    e = mk()
    step(e, 0, fused(left="gallbladder"))
    c = {"name": "mark_controlled", "source": "director", "arguments": {"complication": "bleed"}}
    assert e.validate(1, c) is not None


def test_random_events_fire_and_resolve():
    e = mk()
    e.cfg["random_events"]["enabled"] = True
    e.p["random_per_min"] = 600
    names = []
    for t in range(0, 400):
        names += step(e, t, fused())
    assert any(n in names for n in ("insufflation_loss", "bp_drop", "camera_fog", "smoke"))
    assert "mark_controlled" in names
