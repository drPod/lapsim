"""Rule-based haemodynamics. Not an LLM: bleeding drains blood volume, which drives HR up and
BP down; once the bleed is controlled anaesthesia "resuscitates" and the numbers recover."""
from __future__ import annotations

from dataclasses import dataclass

BASE = dict(hr=78.0, sbp=122.0, dbp=76.0, spo2=99.0, etco2=36.0, rr=12.0)
BLOOD_VOLUME_ML = 5000.0
RESUS_ML_PER_S = 10.0  # effective volume replacement once nothing is bleeding


@dataclass
class Vitals:
    loss_ml: float = 0.0  # uncompensated blood loss
    total_loss_ml: float = 0.0
    bp_drop: float = 0.0  # 0..1 transient anaesthesia hypotension
    insufflation_lost: bool = False
    hr: float = BASE["hr"]
    sbp: float = BASE["sbp"]
    dbp: float = BASE["dbp"]
    spo2: float = BASE["spo2"]
    etco2: float = BASE["etco2"]
    rr: float = BASE["rr"]

    def step(self, dt: float, bleed_rate_ml_s: float, bp_drop_target: float, insufflation_lost: bool):
        if bleed_rate_ml_s > 0:
            self.loss_ml += bleed_rate_ml_s * dt
            self.total_loss_ml += bleed_rate_ml_s * dt
        else:
            self.loss_ml = max(0.0, self.loss_ml - RESUS_ML_PER_S * dt)
        # bp_drop eases toward its target with ~4 s time constant
        self.bp_drop += (bp_drop_target - self.bp_drop) * min(1.0, dt / 4.0)
        self.insufflation_lost = insufflation_lost
        f = min(self.loss_ml / BLOOD_VOLUME_ML, 0.5)
        hr = BASE["hr"] + 150 * f + 25 * self.bp_drop
        sbp = BASE["sbp"] - 160 * f ** 1.5 * 1.2 - 40 * self.bp_drop
        dbp = BASE["dbp"] - 90 * f ** 1.5 * 1.2 - 25 * self.bp_drop
        spo2 = BASE["spo2"] - max(0.0, f - 0.25) * 40
        etco2 = BASE["etco2"] - 30 * f - (6 if insufflation_lost else 0)
        rr = BASE["rr"] + 20 * f
        self.hr, self.sbp, self.dbp = min(hr, 165), max(sbp, 50), max(dbp, 25)
        self.spo2, self.etco2, self.rr = max(spo2, 80), max(etco2, 15), rr

    def event(self, transition_ms: int = 2000) -> dict:
        return {"type": "vitals", "hr": round(self.hr), "bp": f"{round(self.sbp)}/{round(self.dbp)}",
                "spo2": round(self.spo2), "etco2": round(self.etco2), "rr": round(self.rr),
                "transitionMs": transition_ms}

    def summary(self) -> dict:
        return {"hr": round(self.hr), "bp": f"{round(self.sbp)}/{round(self.dbp)}", "spo2": round(self.spo2),
                "etco2": round(self.etco2), "blood_loss_ml": round(self.total_loss_ml)}
