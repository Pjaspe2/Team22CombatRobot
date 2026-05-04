# Combat robot air loop — pneumatic schematic

**Topology:** 3000 psi tank → pressure regulation → **3/2 solenoid** → **single-acting cylinder (spring return)**  
**Fittings:** 1/4 in NPT  
**Regulated-side hose:** SAE J844 PA12 nylon, 1/4 in OD, wall 0.039 in (verify pressure rating at 100 psi working + transients)

---

## Symbol legend (ISO 1219-1 / common ANSI practice)

| Symbol (textual) | Designation | Meaning |
|------------------|-------------|---------|
| ▼ (triangle) | Energy supply | General **pressure / air source** (here: regulated air at valve inlet) |
| Diamond or note | Conditioning | **Filter / regulator / lubricator** as needed (your **regulator** is mandatory) |
| `─X─` in valve envelope | Closed / blocked | Flow path **shut** in that position |
| `─⊥─` or port `R` | Exhaust | **Open to atmosphere** (silencer / muffler recommended) |
| Arrow through valve box | Flow direction | **Working flow** in that shift position |
| `⎍` or coil box on valve | Solenoid | **Electric pilot** actuation |
| `⌐` spring on valve | Return spring | Valve **returns** to rest position when de-energized |
| Single-rod cylinder, spring in cap | Single-acting | **Pneumatic on cap end**, **spring** returns rod |

*Full graphical symbols are normatively defined in **ISO 1219-1**; this document uses labeled boxes and flow lines so you can redraw in CAD or a fluid-power symbol library without ambiguity.*

---

## Functional circuit (single-acting, spring return)

**Ports on a typical 3/2 valve (verify against your datasheet):**

| Port | Common label | Function in this circuit |
|------|----------------|---------------------------|
| Supply | **P** (1) | Regulated **~100 psi** supply |
| Cylinder | **A** (2) | To **cylinder cap** (pressure extends / does work) |
| Exhaust | **R** (3) | **Vent** when cylinder port is connected to exhaust |

**Cylinder:** one pneumatic port on **spring-opposite** side (cap); **rod** side has internal **return spring** (spring retract / return when `A` is vented).

---

## State A — solenoid **de-energized** (valve spring / rest)

Typical **rest** for a **single-acting** setup: **`P` blocked**; **`A` connected to `R`** (exhaust). Cylinder **vents**; **spring** drives the **return** stroke.

```text
                REGULATED (~100 psi)
                        │
                        ▼
     ┌────────────────────────────────────────────┐
     │ 3/2 — ISO directional control valve        │
     │     3 ports, 2 positions                   │
     │     solenoid operated, spring return       │
     ├────────────────────────────────────────────┤
     │  P  ───X──   (no flow from supply)         │
     │              │                             │
     │  A  ─────────┼──► to cylinder port          │
     │              │                             │
     │  R  ◄────────┘   (A open to exhaust)       │
     └────────────────────────────────────────────┘
                        │
                        ▼
                   CYLINDER (single-acting, spring opposing cap pressure)
                     cap ◄── A
                     rod ──►  [──── spring return ────]

Flow summary: **A ↔ R**, **P closed** → spring return.
```

---

## State B — solenoid **energized** (work stroke)

**`P → A` open**; **`R`** not connected to `A`. Cylinder **pressurizes** against the spring for the **power** stroke.

```text
                REGULATED (~100 psi)
                        │
                        ▼
     ┌────────────────────────────────────────────┐
     │ 3/2 — ISO directional control valve        │
     ├────────────────────────────────────────────┤
     │  P  ────────────────────► A  ──► cylinder  │
     │                           │               │
     │  R  ───X──  (exhaust not tied to A)        │
     └────────────────────────────────────────────┘

Flow summary: **P → A**, **R blocked from A** → power stroke vs spring (direction depends on mounting).
```

*Exact **rest vs energized** port connections depend on whether your valve is **normally closed / normally open** to `P→A`; always match **this narrative** to the manufacturer’s **spool diagram**, not guessing from the part headline.*

---

## Upstream of the valve (not shown inside the valve box)

```text
[ 3000 psi TANK ] ──[ HP-rated line only* ]──▶ [ REGULATOR ] ──▶ P (1/4 NPT manifold )
                                                      │
                                                      └──▶ tube to valve P port

* Typical PA12 1/4 OD DOT nylon runs on the **regulated** branch only.
  Tank-to-reg inlet must use **rated HP** hose / tube / fittings.
```

---

## Mounting checklist (single-acting 3/2)

1. **`P`** only sees **regulated** pressure appropriate for cylinder + valve rating.  
2. **`R`** vents to atmosphere — add **noise muffler** if allowed / helpful.  
3. Cylinder’s **remaining port** is not plumbed on a **spring-return** actuator (often a breather/filter on rod side depending on OEM).  
4. Add **proper relief / lockout / tank valve** per safety rules — not drawn here.

---

## Reference

- **ISO 1219-1** — Fluid power systems and components — Graphical symbols and circuit diagrams — Part 1: General graphical symbols  

Use this markdown as a baseline; for event paperwork, redraw the two valve envelopes as true ISO rectangles with official line crossings and actuator symbols from your CAD library.
