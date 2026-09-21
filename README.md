# Terrax

A property marketplace where every listing carries its paperwork: the title
deed, the tax receipt, a valuation calculated from comparable sales with the
comparables shown, and a risk score you can read factor by factor.

Django 5, hand-written CSS, HTMX, Solidity. No build step, no Node, no API keys
required.

```bash
python -m venv .venv
.venv/Scripts/activate          # source .venv/bin/activate on macOS and Linux
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Open <http://localhost:8000>. Sign in as `aarav.mehta` with the password
`demo-terrax-2026`, or as `admin` for the Django admin.

---

## What it does

**For a seller.** A five-step wizard: photographs, the specification, three
compliance documents, a price, and a review. Each step is validated on the
server, so a listing cannot skip ahead with half its fields empty. Documents are
checked as they arrive. Submitting runs the valuation and the risk assessment
and puts the listing in the review queue.

**For a buyer.** Filter the market by city, type, price, bedrooms and area, or
describe what you want in plain language. Compare up to four properties against
their own valuations. Watch a listing and hear about it when the price moves.
Save a search and hear about new matches. Make an offer and negotiate in a
thread both sides keep, or buy a fraction of a property one share at a time.

**Every day.** The dashboard opens on what changed since the last visit: offers
that moved, prices that moved on watched listings, listings that changed status,
shares that settled. Nothing gamified, nothing invented. The reason to come back
is that the state actually changed.

---

## How the valuation works

The number is calculated by `intelligence/engine/comparables.py`, in plain
Python, with no model weights and no network calls.

1. **Select comparables.** Three passes: the same property type in the same
   city, then the same state, then anywhere. Each pass that has to be reached is
   priced as a real loss of relevance.
2. **Score each one.** Size carries 34% of the weight, because price per square
   foot is only meaningful between properties of a similar size. Then location
   at 26%, recency at 15%, configuration at 14%, age at 11%.
3. **Rebase across cities.** A comparable from another city is put on the target
   city's price level before its rate is used. Without this step a one bedroom
   flat in Bandra is compared against a flat in Kothrud and comes out at a third
   of its value.
4. **Blend and trim.** A similarity-weighted mean, with anything more than two
   standard deviations from the median dropped first when there are five or more
   comparables.
5. **Adjust.** Age, furnishing, amenity count, aspect, floor position, title type
   and carpet ratio. Only differences that were actually observed produce an
   adjustment.
6. **Publish a range, not a point.** The interval widens with the scatter of the
   comparable rates and narrows with the square root of the sample size.

The comparables used, their weights, and every adjustment are shown on the
listing page. The risk score works the same way: seven weighted factors totalling
one hundred, each shown with its points and the reason it fired.

---

## The language-model layer

Terrax runs with no API key. Every feature that can use a model has a local
implementation that runs instead, and the interface says which one produced the
text.

| Feature | Without a model | With one |
| --- | --- | --- |
| Valuation explanation | Assembled from the comparables and adjustments | Rewritten as prose from the same figures |
| Listing description | Built from the structured fields | Written from the same fields |
| Questions about a listing | Matched against the record | Answered from the record, with the record in the prompt |
| Plain-language search | Parsed with rules | Parsed by the model, then validated against the same allow-list |

**The model never decides a number.** It is handed the finished valuation and
told to explain it, with an explicit instruction not to state a different
figure. Search output is validated against the same keys the search form
accepts before it is trusted. A failure at any point falls back to the local
path rather than surfacing as a broken feature.

Four providers, selected by one environment variable, all with a free tier:

```bash
LLM_PROVIDER=groq        # llama-3.3-70b-versatile, 30 req/min, 1000 req/day
LLM_PROVIDER=gemini      # gemini-2.5-flash, Google AI Studio free tier
LLM_PROVIDER=openrouter  # any :free model, 50 req/day
LLM_PROVIDER=ollama      # local, offline, unlimited
LLM_PROVIDER=none        # the default
```

Adding a fifth means writing one `_call` method. Nothing in the application
imports a concrete provider; everything goes through `get_provider()`.

---

## Layout

```
terrax/          settings split into base / dev / prod
core/           base layout, design tokens, dashboard, content pages, errors
accounts/       profile, identity, wallet, watchlist, saved searches, notifications
properties/     Listing, media, documents, the wizard, search and filtering
market/         offers, negotiation, fractional shares, the ledger, portfolio
intelligence/   the valuation engine, risk scoring, document checks, the LLM layer
chain/          the Solidity contracts, IPFS pinning, deeds and shares
```

Views resolve the request, delegate to their app's `services.py`, and render.
Business rules live in the service layer. Validation lives in forms. Choices
live in `constants.py` rather than as strings scattered through templates.

### Ownership is a query, not a conditional

```python
def owned_listing_or_403(user, public_id) -> Listing:
    try:
        return Listing.objects.get(public_id=public_id, owner=user)
    except (Listing.DoesNotExist, ValueError, TypeError) as exc:
        raise PermissionDenied("That listing does not belong to you.") from exc
```

There is no code path that loads someone else's row and then decides whether to
allow it. The same holds for offers, which are scoped to the two parties by
`Offer.objects.for_user(user)`.

Public identifiers are UUIDs, so the catalogue cannot be walked by incrementing
a number in the address bar.

---

## Why there is no build step

The first version of this project loaded Tailwind from `cdn.tailwindcss.com`,
which ships a CSS compiler as a 400KB JavaScript bundle and runs a full compile
in the browser on every page load. Nothing was cached between navigations
because the work was recomputed rather than fetched. That is the single largest
reason the old build felt slow.

What replaced it:

| | Before | After |
| --- | --- | --- |
| CSS | Compiled in the browser, every load | Four static files, cached immutably |
| Fonts | Three static weights from Google Fonts | Two self-hosted variable files, 51KB total |
| Icons | A 120KB webfont for twelve glyphs | One 33KB SVG sprite |
| Filtering | Full page reload | HTMX swaps the results fragment |
| Images | Remote originals, no dimensions | Local WebP thumbnails with explicit sizes |
| Requests to third parties at runtime | Four, on every page | None, except map tiles on the map page |

Filtering the marketplace re-renders one fragment. The shell, the stylesheet
and the fonts stay mounted, which is why changing a filter does not feel like a
page load. The URL still updates, so a filtered view is shareable and the back
button works. Without JavaScript it degrades to a plain GET form.

Static files are served by WhiteNoise with `CompressedManifestStaticFilesStorage`,
so filenames are content-hashed and can carry a one-year cache header.

Measured on the marketplace with twelve listings on screen: 11 requests, 89KB
over the wire, DOMContentLoaded at 59ms, and a cumulative layout shift of zero.
Leaflet is vendored into `static/vendor/` and loaded only on the map page, so no
other page pays for it. The only request that leaves the domain at runtime is
for OpenStreetMap tiles, which needs a tile server by definition.

---

## The contracts

Two contracts in `chain/contracts/`, written in Solidity 0.8.28, compiled from
Python and executed by the test suite.

### What goes on chain, and what does not

Not the documents. A title deed on a public ledger is expensive and a privacy
failure at the same time. What is recorded is the pair that makes the off-chain
bundle checkable:

| | |
| --- | --- |
| `metadataCID` | where the evidence bundle is published, as an IPFS identifier |
| `evidenceHash` | keccak256 of that bundle, canonicalised with sorted keys |

Verification is therefore three steps with no special tools: fetch the bundle,
serialise it with sorted keys and no whitespace, hash it, compare. Change one
document, one figure or one word and the hashes stop matching. Every recorded
listing carries a button that runs exactly this against the live contract.

### PropertyDeed, an ERC-721

One token per verified property. Only the issuer can `record`, because Terrax
checks documents and identity before a listing publishes, and an open mint would
let anyone record a property nobody checked and inherit the credibility of the
ones that were. A re-valuation calls `updateEvidence`, which writes the new hash
and leaves the previous one in the event log: the history is extended, never
rewritten.

### PropertyShares, an ERC-1155

Fractional ownership, where the token id **is** the deed's token id, so a share
and the property it is a share of are joined by the number rather than by a
lookup table that can drift. One contract for every property: one deployment,
one approval.

The supply is fixed when the pool opens and cannot be raised, because raising it
after people have bought in dilutes them silently. `issue` is the primary sale
that `market.services.buy_shares` performs off chain, and `redeem` is the exit
that `sell_shares` performs. The invariant the Python tests assert, that issued
shares never exceed the supply, is enforced here by the contract rather than by
convention.

Both are written out rather than imported from OpenZeppelin, because the project
carries no Node toolchain and because a reader should be able to finish the
file. Production would use the audited library.

### No Node, anywhere

`py-solc-x` fetches the pinned compiler and `chain/compiler.py` drives it, so
the build step is `python manage.py compile_contracts` rather than Hardhat or
Foundry. Artifacts are cached under a hash of the source, which is what stops a
deployment drifting from the file on disk, and they are committed, so running
the site needs no compiler at all.

```
PropertyDeed      5,456 bytes  22.2% of the EIP-170 limit  26 ABI entries
PropertyShares    6,025 bytes  24.5% of the EIP-170 limit  23 ABI entries
```

### Going on chain

```bash
python manage.py deploy_contracts    # prints the two addresses to put in .env
python manage.py sync_chain          # records every published listing
```

Point `WEB3_RPC_URL` at a node and `WEB3_PRIVATE_KEY` at a throwaway account
with test funds first; the chain section of `.env.example` has the faucet link.

`deploy_contracts` simulates both deployments and refuses if the balance will
not cover the real gas price, rather than sending a transaction that runs out
halfway. `sync_chain` is idempotent: a deed already recorded is re-checked and
not re-recorded, a pool already open is left alone, and a holder whose on-chain
balance matches the ledger is skipped. Running it twice does nothing the second
time, which is what makes it safe on a timer.

**The web request never waits on a chain.** Publishing writes the local record
and returns. `sync_chain` sends the transactions. A listing page that hangs for
eleven seconds because a testnet is congested is a worse failure than a listing
that says "not yet recorded".

### With none of it configured

Which is how the project ships. The site is fully functional: every property
gets a record, that record carries a real evidence hash, and the listing page
labels it **Off chain** and says in as many words that it is a reference number
and not proof of title. `contract_address`, `tx_hash` and `chain_token_id` stay
empty, and `TokenRecord.is_onchain` reads those three columns rather than a
label, so nothing can claim to be on a chain without a mined transaction behind
it.

An earlier version filled `tx_hash` with `secrets.token_hex(32)`. That renders
as a convincing transaction hash pointing at nothing, which is a worse lie than
an empty column.

### How the contracts are tested

`eth-tester` and `py-evm` give the suite a real EVM in memory. solc compiles the
Solidity and py-evm executes the bytecode, so reverts are real reverts and gas
is real gas: `tests/test_chain.py` asserts on revert reasons, not on what a mock
was told to say. It covers minting, issuer-only access, evidence updates, deed
transfers, ERC-165 advertisement, pool supply limits, oversell refusal,
redemption, share transfers between holders, and the full
record-verify-change-verify cycle.

The compiled bytecode was also simulated against a live Polygon Amoy node, which
executed both constructors and returned valid runtime code.

---

## Deploying

The project runs on Vercel. Two things a serverless host cannot give a Django
application are a writable disk and a process that stays alive, and both are
configured away rather than assumed.

```
api/index.py     the object Vercel imports: Django's own WSGI application
vercel.json      the build command and the single route
.vercelignore    keeps the virtualenv, tests and media out of the bundle
```

| | Locally | Deployed |
| --- | --- | --- |
| Database | SQLite | Postgres, from `DATABASE_URL` |
| Static files | served from `/static` | collected at build, hashed, immutable |
| Uploads | `media/` on disk | Cloudinary, from `CLOUDINARY_URL` |
| Hostname | `*` | read from `VERCEL_URL` at startup |

Set three environment variables in the Vercel project — `SECRET_KEY`,
`DATABASE_URL`, `CLOUDINARY_URL` — and push. `ALLOWED_HOSTS` and
`CSRF_TRUSTED_ORIGINS` are derived from the deployment's own hostname, which
changes on every preview build and so cannot be a literal.

Then, once, from a machine that can reach the database:

```bash
DATABASE_URL=... python manage.py migrate --settings=terrax.settings.prod
DATABASE_URL=... python manage.py seed_demo --settings=terrax.settings.prod
```

### What the deployment was checked against

Not just that it builds. `api/index.py` was called directly with the request
environment Vercel's proxy produces, under production settings:

- Six pages render, including the marketplace off a real query.
- `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` and a
  one-year HSTS header are present on the response.
- Plain HTTP is redirected to HTTPS via `X-Forwarded-Proto`, which is the only
  way the app can know the scheme behind a proxy that terminates TLS.
- An unrecognised `Host` is refused with a 400 rather than served.
- A hashed static file is served with `max-age=315360000, public, immutable`.
- An unknown listing is a branded 404 with no traceback in the body.

That run also caught the one thing that would have failed the build:
`collectstatic` aborts on the vendored `leaflet.js`, which ends with a pointer
to a sourcemap that upstream does not distribute. WhiteNoise treats a dangling
reference as an error, correctly — the fix was to drop the reference rather
than to relax the check.

### Cold start

`web3` takes about a second and a half to import and is reached from
`properties.services`, which every listing page touches. It is imported inside
the functions that need it instead, so a request that never opens a socket to a
node never pays for loading one. Django's own start-up is around half a second.

---

## Tests

```bash
pytest          # 176 tests
ruff check .
python manage.py check --deploy --settings=terrax.settings.prod
```

The suite covers the valuation engine (including the cross-city rebasing and the
land-versus-flat case), risk scoring, document checks, wizard validation,
ownership boundaries, offers, share trading, portfolio arithmetic, ledger
conservation, search filtering, plain-language parsing, both Solidity contracts,
and that every page renders from the database.

`tests/test_permissions.py` reproduces each hole that existed in the previous
build: anonymous writes, cross-user access to a listing, cross-user access to a
negotiation, and logout over GET.

---

## Commands

```bash
python manage.py seed_demo --reset       # 24 listings, 10 accounts, offers, holdings
python manage.py recompute               # refresh valuations and risk scores
python manage.py compile_contracts       # solc, with sizes against the EIP-170 limit
python manage.py deploy_contracts        # deploy to the configured chain
python manage.py sync_chain --dry-run    # what would be sent, sending nothing
```

`seed_demo` writes nothing directly. Listings go through `submit_for_review` and
`publish`, valuations come from the comparables engine, offers go through the
offer service, and a few holders sell part of their position so the ledger's
secondary side is exercised rather than left dormant. Photographs come from a
local pool in `static/img/seed`, so the seed runs with no network connection.

Valuations are comparisons, so adding listings makes older estimates stale. That
is why `seed_demo` finishes by calling `recompute`, and why a deployment would
run it on a schedule.

---

## Design

One accent colour (a deep forest green, carried over from the original brand),
one neutral family, one radius scale, one motion curve. Every value is declared
once in `static/css/tokens.css` and referenced by name everywhere else, so a
theme change is a change to one file.

Light and dark are both first class, following `prefers-color-scheme` with a
manual toggle that persists. The theme is resolved by an inline script before
the first paint, so a dark-mode user never sees a white flash.

Numbers render in Geist Mono with tabular figures, so price columns line up.
Prices use lakh and crore groupings, because that is what the readers of an
Indian property site expect.

Motion honours `prefers-reduced-motion` and animates only `transform` and
`opacity`. Content that is already on screen is shown immediately rather than
faded in, because fading in something the user is already looking at is a delay,
not an entrance.

---

## What this is not

A student project, not a licensed marketplace. The listings loaded by default
are seeded demonstration data with fabricated addresses and documents. Identity
documents are reviewed by hand rather than by a verification provider. Accepted
offers and share purchases write records; no money moves and no property changes
hands. Valuations illustrate a method; they are not professional valuations.

The contracts are unaudited and are meant for a testnet. Recording a deed proves
that what you are shown now is what was recorded then. It does not prove that a
document is genuine, that the seller owns the property, or that the valuation is
right. Those are what the document checks, the risk score and the identity
review are for, and those are judgements rather than proofs.
