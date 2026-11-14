# Live demo script (about 8 minutes)

Start fresh: sidebar → **Reset demo data**. Use Claude (live) if a key is set. The clock reads
**Tue Nov 10 · 10:50 AM**.

## 0 · Set the scene (45 s): Club data tab

- "This is the club's real occupancy profile: weekday evenings and weekend mornings run above 80%;
  weekday early afternoons drop to around 20%."
- "Rally derives opening hours and 90-minute slots from it. A slot is off-peak below 40% (dots).
  Those are the only slots where it tries to sell an empty court."
- Point at the court sheet: blue cells are today's empty off-peak courts; orange cells are open
  matches with a free seat.

## 1 · Missing fourth player (2.5 min): Missing players tab

1. **Court 2 · 5:30–7:00 PM** is selected: "Ethan dropped out at 10:12. Rally detected it on its
   own; nothing has been sent."
2. Walk the shortlist: Priya is first (exact level, prefers evenings, has played with two of the three).
   Open **Excluded players**: Grace has no opt-in, Jun opted out, Omar hit the 2-per-week cap, Aisha
   is already booked at 5:30.
3. Open **Checked but not actionable**: the 11:30 match starts in 40 min (under the 45-minute lead
   time); the 7 PM private match is excluded by policy.
4. Leave the top 3 ticked → **Approve this outreach**. "The approval covers exactly these three players,
   this text, three invitations, until 4:45 PM."
5. **Player chat** → Priya → 👎 *Can't*. Show the structured output (`DECLINE`, safe) and the Python
   decision: Rally invites Marcus next, one at a time.
6. Marcus → 👍 *Yes*. State is `AWAITING_BOOKING_VERIFICATION`: "A yes is not a booking."
7. Back to **Missing players** → **✅ Simulate provider confirmation (paid)** → `CONFIRMED`.
   "Only the provider's verified booking produces CONFIRMED."

## 2 · Empty off-peak court (2.5 min): Empty courts tab

1. Open **Court 1 · 2:30–4:00 PM**: "Typical occupancy 20%, 5 of 6 courts free, about 3.5 hours out."
2. Group note: a level 2.7–3.3 band, all six prefer weekday afternoons, five pairs have played together.
   "It forms a compatible four, not four unrelated high scores."
3. **Approve this outreach** (first wave of 6).
4. **Player chat**: Ruth, Carmen, Gabriel → 👍; Felipe → 👎; Anika → 👍. On the fourth yes Rally
   rechecks the court and sends booking links. Yuki → 👍 is **waitlisted**: no seat is given twice.
5. "Four yes messages, and still not a sale." Click **💳 Simulate booking with payment pending**:
   still pending. Then **✅ Simulate provider confirmation (paid)** → `CONFIRMED`.

## 3 · Safety (2 min)

1. **Missing players → Court 6 · 8:30 PM** → approve.
2. **🧪 Safety drills**: *message a player who is not approved* → blocked and logged;
   *send the same invite twice* → duplicate suppressed.
3. **Player chat** → Noah → 🛑 *STOP*: consent revoked by a fixed rule, Rally moves on to Dev.
   Dev → 🤷 *Maybe*: `TENTATIVE` → campaign `ESCALATED`, no booking link. Back on the opportunity:
   **⏭ Skip to next approved player** → Isabel.
   Isabel → 🧨 *Injection*: Claude labels it `UNKNOWN`/`requires_manager`; nothing progresses, and no
   tool exists that could change a price or booking anyway.
4. Stale court: **Empty courts → Court 1 · 1:00 PM** → approve → one player says yes →
   **🏃 Someone else books this court** → `ESCALATED`, held players get an apology, outreach stops.
5. Optional: sidebar **Simulate provider outage** while a booking is pending → `UNVERIFIED`, not
   `CONFIRMED`.

## Close (30 s): Activity and traces

- Filter to one campaign: every step shows the actor (code / llm / manager / player / provider), the
  tool, the state change and the policy version. "Append-only, exportable, and the base for our 20
  golden-case evaluation."
- The header line says it plainly: all data is fictional except the occupancy profile, and no revenue is claimed.
