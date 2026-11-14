# Rally commercial case: price, margin and break-even

Rally is sold to the club (general manager or operations lead) as a monthly subscription. At the
hypothesised **$199–$299 per month** it costs **$18–$55 per month** to run, a gross margin of
**72–94%**. A club breaks even on the subscription with **2–3 extra paid off-peak bookings a month**,
under 1% of the empty off-peak capacity in the club's own occupancy data.

Every figure is labelled. "Assumption" means a pilot hypothesis from the PRD, not a measured result.
No revenue, demand or willingness to pay is claimed.

## 1. Inputs

| Input | Value | Status | Source |
|---|---|---|---|
| Subscription price per club | $199–$299 / month | assumption (untested price hypothesis) | PRD section 2 |
| Cost to serve one club | $18–$55 / month | estimate from list prices and measured LLM cost | `docs/unit_economics.md` |
| Court price | $80 / court-hour | assumption (illustrative) | PRD section 10, `data/club_policy.yaml` |
| Revenue per newly booked 90-minute slot | $120 gross | derived ($80 × 1.5 h) | PRD section 10 |
| Courts | 6 | demo club data | `data/courts.csv` |
| Off-peak slots per court | 27 of 77 weekly 90-minute slots | derived from the club's occupancy spreadsheet (below 40% full) | `data/occupancy_profile.csv` |
| Empty off-peak court-slots | ≈ 130 / week ≈ 560 / month | derived: off-peak slots × (1 − typical occupancy), all courts | same |

## 2. Rally's margin per club (vendor view)

| Scenario | Price | Cost to serve | Gross profit / month | Gross margin |
|---|---|---|---|---|
| Low price, high cost (SMS, Sonnet, production hosting) | $199 | $55 | $144 | 72% |
| Middle | $249 | $37 | $212 | 85% |
| High price, low cost (WhatsApp, Haiku, pilot hosting) | $299 | $18 | $281 | 94% |

Hosting ($13–$44 of the cost) is shared across clubs once several clubs run on one deployment, so margin
rises with scale. Not included: sales and onboarding cost per club, support time, and any Playtomic
Connect partner fee (none published). These are open questions for the pilot.

## 3. Break-even for the club (customer view)

Bookings needed per month to cover the subscription = price ÷ value per incremental booking.

| Subscription | On gross revenue ($120 / booking) | If the club keeps 70% of it ($84 / booking, assumption) |
|---|---|---|
| $199 / month | 1.7 → **2 bookings** | 2.4 → **3 bookings** |
| $299 / month | 2.5 → **3 bookings** | 3.6 → **4 bookings** |

Against ≈ 560 empty off-peak court-slots a month, 2–4 extra bookings is **0.4–0.7%** of that empty
capacity. The PRD's "+10 percentage-point fill-rate lift" is a separate, unproven upside hypothesis: it
would be about 70 extra bookings a month (≈ $8,400 gross, not profit) and is not used in the case.

## 4. What does not count as revenue

- **Missing-player replacements** usually add **$0** new court revenue: the match was already paid. Their
  value is organiser time saved and cancellations avoided, counted only when the match was actually at
  risk (PRD section 10).
- **Interest is not revenue.** Rally counts a booking only when the booking provider shows it paid
  (golden cases G13, G17).
- **Incremental means compared with doing nothing.** The pilot needs a control group (club's normal
  process) to show which bookings Rally created.

## 5. How the pilot would test the case

Randomise eligible empty off-peak slots between the club's normal process and Rally for 4–6 weeks.
Measure paid bookings, net contribution after discounts and messaging cost, opt-outs and cancellations.
Go when Rally's arm shows more paid bookings than the subscription costs, without more opt-outs or
cancellations. Charge the subscription only after that result.
