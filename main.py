"""
SupplyChain Sentinel — FastAPI Backend
Serves the frontend and exposes REST API endpoints.

Run:  py main.py
Open: http://localhost:3000
"""

import copy
import csv
import hashlib
import io
import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Import sample data ──────────────────────────────────────────────────────
import sys
sys.path.insert(0, str(Path(__file__).parent))
from api.data import SHIPMENTS as _SAMPLE, WAREHOUSES

# Working copy — mutated by API calls, reset reloads from _SAMPLE
_shipments: list[dict] = copy.deepcopy(_SAMPLE)

# ── App setup ───────────────────────────────────────────────────────────────
app = FastAPI(title="SupplyChain Sentinel API", version="1.0.0")

TODAY = date(2026, 10, 10)


# ── Helpers ─────────────────────────────────────────────────────────────────
def _days_overdue(date_str: str) -> int:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return max(0, (TODAY - d).days)
    except Exception:
        return 0


def _days_left(date_str: str) -> int:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (d - TODAY).days
    except Exception:
        return 0


def _record_fingerprint(s: dict) -> str:
    """Deterministic string of key fields for SHA-256 hashing."""
    fields = {
        "id": s["id"],
        "product": s["product"],
        "originCity": s["origin"]["city"],
        "originCountry": s["origin"]["country"],
        "destCity": s["destination"]["city"],
        "destCountry": s["destination"]["country"],
        "dispatchDate": s["dispatchDate"],
        "expectedDelivery": s["expectedDelivery"],
        "weight": s["weight"],
        "value": s["value"],
        "units": s["units"],
        "carrier": s["carrier"],
    }
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def _compute_hashes() -> None:
    """Attach _baselineHash, _currentHash, _hashMismatch to every shipment."""
    for s in _shipments:
        h = _record_fingerprint(s)
        s.setdefault("_baselineHash", h)
        s["_currentHash"] = h
        s["_hashMismatch"] = s["_baselineHash"] != s["_currentHash"]


def _detect_risks() -> list[dict]:
    risks: list[dict] = []
    for s in _shipments:
        is_del = s["status"] == "Delivered"
        od = _days_overdue(s["expectedDelivery"])
        dl = _days_left(s["expectedDelivery"])
        pending = [c for c in s["checkpoints"] if not c.get("completed")]

        if not is_del and od > 0:
            risks.append({
                "severity": "high", "type": "Overdue Delivery",
                "shipmentId": s["id"], "product": s["product"],
                "reason": f"Expected {s['expectedDelivery']}, {od}d overdue.",
                "evidence": f"Status: {s['status']} | Location: {s['currentLocation']}",
            })
        if not is_del and od == 0 and dl <= 2 and len(pending) > 1:
            risks.append({
                "severity": "medium", "type": "Delivery At Risk",
                "shipmentId": s["id"], "product": s["product"],
                "reason": f"{dl}d to ETA, {len(pending)} checkpoints pending.",
                "evidence": f"Location: {s['currentLocation']}",
            })
        for cp in s["checkpoints"]:
            if not cp.get("completed") and cp.get("expected") and _days_overdue(cp["expected"]) > 0:
                risks.append({
                    "severity": "high" if cp.get("delayed") else "medium",
                    "type": "Missed Checkpoint",
                    "shipmentId": s["id"], "product": s["product"],
                    "reason": f"\"{cp['stage']}\" expected {cp['expected']}, {_days_overdue(cp['expected'])}d overdue.",
                    "evidence": cp.get("delayReason") or f"Location: {cp['location']}",
                })
            if cp.get("missing") and not cp.get("completed"):
                risks.append({
                    "severity": "critical", "type": "Missing Checkpoint Record",
                    "shipmentId": s["id"], "product": s["product"],
                    "reason": f"\"{cp['stage']}\" absent from system.",
                    "evidence": cp.get("delayReason") or f"Location: {cp['location']}",
                })
        if s.get("_hashMismatch"):
            risks.append({
                "severity": "critical", "type": "Record Integrity Alert",
                "shipmentId": s["id"], "product": s["product"],
                "reason": "SHA-256 hash mismatch — record changed after baseline was set.",
                "evidence": f"Baseline: {s.get('_baselineHash','')[:16]}… | Current: {s.get('_currentHash','')[:16]}…",
            })
        # Inconsistent checkpoint sequence
        found_incomplete = False
        for cp in s["checkpoints"]:
            if not cp.get("completed"):
                found_incomplete = True
            elif found_incomplete:
                risks.append({
                    "severity": "medium", "type": "Inconsistent Sequence",
                    "shipmentId": s["id"], "product": s["product"],
                    "reason": "A later checkpoint is 'completed' while an earlier one is not.",
                    "evidence": "Check checkpoint timeline.",
                })
                break

    for w in WAREHOUSES:
        pct = w["used"] / w["capacity"]
        if pct >= 0.9 and w["status"] == "Online":
            risks.append({
                "severity": "medium", "type": "Warehouse Capacity Warning",
                "shipmentId": None, "warehouseId": w["id"],
                "product": None,
                "reason": f"{w['name']} at {round(pct*100)}% capacity.",
                "evidence": f"Used: {w['used']:,} / {w['capacity']:,} | Region: {w['region']}",
            })
    return risks


# ── Pydantic models ──────────────────────────────────────────────────────────
class ShipmentUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    currentLocation: Optional[str] = None
    checkpointIndex: Optional[int] = None   # mark this checkpoint done


# ── API Routes ───────────────────────────────────────────────────────────────

@app.get("/api/shipments")
def get_shipments(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    q: Optional[str] = None,
):
    """Return all shipments, optionally filtered."""
    result = _shipments
    if status:
        result = [s for s in result if s["status"] == status]
    if priority:
        result = [s for s in result if s["priority"] == priority]
    if q:
        ql = q.lower()
        result = [
            s for s in result
            if ql in s["id"].lower()
            or ql in s["product"].lower()
            or ql in s["origin"]["city"].lower()
            or ql in s["destination"]["city"].lower()
            or ql in s["carrier"].lower()
            or ql in s["currentLocation"].lower()
        ]
    # Strip internal fields before sending
    return [_public(s) for s in result]


@app.get("/api/shipments/{shipment_id}")
def get_shipment(shipment_id: str):
    s = _find(shipment_id)
    return _public(s)


@app.patch("/api/shipments/{shipment_id}")
def update_shipment(shipment_id: str, body: ShipmentUpdate):
    """Update status, priority, location, or mark a checkpoint done."""
    s = _find(shipment_id)
    if body.status:
        s["status"] = body.status
    if body.priority:
        s["priority"] = body.priority
    if body.currentLocation:
        s["currentLocation"] = body.currentLocation
    if body.checkpointIndex is not None:
        idx = body.checkpointIndex
        if 0 <= idx < len(s["checkpoints"]):
            s["checkpoints"][idx]["completed"] = True
            s["checkpoints"][idx]["date"] = str(TODAY)
            s["checkpoints"][idx].pop("delayed", None)
            s["checkpoints"][idx].pop("missing", None)
    # Recompute hash
    s["_currentHash"] = _record_fingerprint(s)
    s["_hashMismatch"] = s.get("_baselineHash", "") != s["_currentHash"]
    return _public(s)


@app.post("/api/shipments/{shipment_id}/tamper")
def tamper_shipment(shipment_id: str):
    """Demo: mutate a field so the hash mismatch is detectable."""
    s = _find(shipment_id)
    if s.get("_tampered"):
        raise HTTPException(400, "Already tampered. Restore first.")
    s["_originalValue"] = s["value"]
    s["_originalUnits"] = s["units"]
    old_val = int(s["value"].replace("$", "").replace(",", ""))
    s["value"] = f"${old_val + 77777:,}"
    s["units"] = s["units"] + 999
    s["_tampered"] = True
    s["_currentHash"] = _record_fingerprint(s)
    s["_hashMismatch"] = s.get("_baselineHash", "") != s["_currentHash"]
    return {"message": f"Tamper applied to {shipment_id}", **_public(s)}


@app.post("/api/shipments/{shipment_id}/restore")
def restore_shipment(shipment_id: str):
    """Restore a tampered shipment to its pre-tamper state."""
    s = _find(shipment_id)
    if s.get("_originalValue") is not None:
        s["value"] = s.pop("_originalValue")
    if s.get("_originalUnits") is not None:
        s["units"] = s.pop("_originalUnits")
    s["_tampered"] = False
    s["_currentHash"] = _record_fingerprint(s)
    s["_hashMismatch"] = s.get("_baselineHash", "") != s["_currentHash"]
    return {"message": f"{shipment_id} restored", **_public(s)}


@app.post("/api/reset")
def reset_all():
    """Reset all shipments to sample data."""
    global _shipments
    _shipments = copy.deepcopy(_SAMPLE)
    _compute_hashes()
    return {"message": "All data reset to sample."}


@app.get("/api/warehouses")
def get_warehouses(region: Optional[str] = None):
    result = WAREHOUSES if not region else [w for w in WAREHOUSES if w["region"] == region]
    return result


@app.get("/api/risks")
def get_risks(severity: Optional[str] = None, shipmentId: Optional[str] = None):
    risks = _detect_risks()
    if severity:
        risks = [r for r in risks if r["severity"] == severity]
    if shipmentId:
        risks = [r for r in risks if r.get("shipmentId") == shipmentId]
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    risks.sort(key=lambda r: order.get(r["severity"], 9))
    return risks


@app.get("/api/stats")
def get_stats():
    """KPI summary for the dashboard."""
    delivered = sum(1 for s in _shipments if s["status"] == "Delivered")
    in_transit = sum(1 for s in _shipments if s["status"] == "In Transit")
    delayed = sum(1 for s in _shipments if s["status"] == "Delayed")
    at_risk = sum(1 for s in _shipments if s["status"] == "At Risk")
    risks = _detect_risks()
    cap_warnings = sum(1 for r in risks if r["type"] == "Warehouse Capacity Warning")
    integrity_alerts = sum(1 for s in _shipments if s.get("_hashMismatch"))
    online_wh = [w for w in WAREHOUSES if w["status"] == "Online"]
    total_cap = sum(w["capacity"] for w in online_wh)
    total_used = sum(w["used"] for w in online_wh)
    return {
        "total": len(_shipments),
        "delivered": delivered,
        "inTransit": in_transit,
        "delayed": delayed,
        "atRisk": at_risk,
        "activeAlerts": len(risks),
        "criticalAlerts": sum(1 for r in risks if r["severity"] == "critical"),
        "capacityWarnings": cap_warnings,
        "integrityAlerts": integrity_alerts,
        "onlineWarehouses": len(online_wh),
        "totalWarehouses": len(WAREHOUSES),
        "totalCapacity": total_cap,
        "usedCapacity": total_used,
        "capacityPct": round(total_used / total_cap * 100) if total_cap else 0,
    }


@app.get("/api/integrity")
def get_integrity():
    """Return hash verification status for every shipment."""
    return [
        {
            "id": s["id"],
            "product": s["product"],
            "baselineHash": s.get("_baselineHash", ""),
            "currentHash": s.get("_currentHash", ""),
            "verified": not s.get("_hashMismatch", False),
            "tampered": s.get("_tampered", False),
        }
        for s in _shipments
    ]


@app.get("/api/export/shipments")
def export_shipments_csv():
    """Download all shipments as CSV."""
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["Tracking ID","Product","Category","Origin City","Origin Country",
                "Dest City","Dest Country","Current Location","Carrier",
                "Dispatch","Expected Delivery","Status","Priority",
                "Weight","Value","Units","Checkpoints Done","Total Checkpoints"])
    for s in _shipments:
        done = sum(1 for c in s["checkpoints"] if c.get("completed"))
        w.writerow([s["id"], s["product"], s["category"],
                    s["origin"]["city"], s["origin"]["country"],
                    s["destination"]["city"], s["destination"]["country"],
                    s["currentLocation"], s["carrier"],
                    s["dispatchDate"], s["expectedDelivery"],
                    s["status"], s["priority"],
                    s["weight"], s["value"], s["units"],
                    done, len(s["checkpoints"])])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=scs_shipments_{TODAY}.csv"},
    )


@app.get("/api/export/risks")
def export_risks_csv():
    """Download risk alerts as CSV."""
    risks = _detect_risks()
    output = io.StringIO()
    w = csv.writer(output)
    w.writerow(["Severity","Type","Shipment ID","Warehouse ID","Reason","Evidence"])
    for r in risks:
        w.writerow([r["severity"], r["type"], r.get("shipmentId") or "",
                    r.get("warehouseId") or "", r["reason"], r["evidence"]])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=scs_risks_{TODAY}.csv"},
    )


# ── Serve frontend ───────────────────────────────────────────────────────────
_BASE = Path(__file__).parent

# Serve static assets (CSS, JS files if any)
if (_BASE / "static").exists():
    app.mount("/static", StaticFiles(directory=_BASE / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    """Serve the main dashboard."""
    return (_BASE / "index.html").read_text(encoding="utf-8")


# ── Private helpers ──────────────────────────────────────────────────────────
def _find(sid: str) -> dict:
    s = next((x for x in _shipments if x["id"] == sid), None)
    if not s:
        raise HTTPException(404, f"Shipment {sid} not found.")
    return s


def _public(s: dict) -> dict:
    """Strip internal _* fields except the ones the frontend needs."""
    keep = {"_hashMismatch", "_baselineHash", "_currentHash", "_tampered"}
    return {k: v for k, v in s.items() if not k.startswith("_") or k in keep}


# ── Startup ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
def on_startup():
    _compute_hashes()
    print("=" * 55)
    print("  SupplyChain Sentinel — FastAPI backend running")
    print("  Dashboard  -> http://localhost:3000")
    print("  API docs   -> http://localhost:3000/docs")
    print("=" * 55)


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
