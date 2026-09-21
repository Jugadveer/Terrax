# Terrax rebuild plan

Written 2026-09-17, after a full hands-on audit of the running site.

---

## Part 1. What the audit found

The app boots, and roughly three things genuinely work: signup/login/logout, the
5-step listing wizard writing rows to SQLite, and the Pinata IPFS upload when
credentials exist. Everything else is either hardcoded, unwired, or unsafe.

### 1.1 Data that is fake

| Surface | Reality |
| --- | --- |
| Marketplace grid | 5 hardcoded HTML cards. The 19 real listings in the database are invisible. |
| Marketplace list view | 5 more hardcoded cards, duplicating the grid markup by hand. |
| Landing "Featured Properties" | 3 hardcoded cards. Card 2's Unsplash URL returns a photo of sneakers. |
| Dashboard metrics | `$11.4M`, `2`, `+15.3%`, `12.5 ETH` are literals in the template. Identical for every user, including anonymous visitors. |
| Dashboard "My Listings" | 2 hardcoded properties. A user's own listings never appear. |
| Sales tracking | Permanent empty state. No model exists behind it. |
| Earnings | Hardcoded `12.5 ETH` plus a withdraw button with no handler. |

### 1.2 Controls that do nothing

Verified in the browser by enumerating handlers on every `<button>` and `<a>`:

- Search box: no event listener, no form, no query parameter. Typing does nothing.
- "All Locations", "All Types", filter icon: plain buttons, no dropdown, no handler.
- "View Details" x7 and "Compare" x5: no handler. No detail page exists in the URLconf.
- Dashboard "Edit" x2 and "Withdraw": no handler.
- 16 links pointing at `#`: About, Contact, Support, Help Center, Guides, Privacy Policy, Terms of Service, all four social icons, "Join as Seller/Buyer/Investor".
- Newsletter form calls `alert("Thank you for subscribing!")` and discards the email.
- Every `<button>` on the site omits `type="button"`, so all of them default to
  `type="submit"`.

### 1.3 Security holes (each reproduced against the running server)

1. `save_listing_step` has no `@login_required`. An anonymous POST hits
   `Listing.objects.create(owner=request.user)` with `AnonymousUser` and returns a
   **500 with a full Django debug traceback**, which leaks settings and the secret key.
2. `submit_listing` has no `@login_required` and no owner check. It fetches by raw id
   via `get_object_or_404(Listing, id=listing_id)` and proceeds to the IPFS upload.
   Confirmed: `testuser` (id 12) reached listing 21, owned by user 1. With valid Pinata
   keys, any visitor could flip any listing to `submitted` and drain the owner's quota.
3. Neither endpoint checks the HTTP method.
4. `logout_view` accepts GET, so any third-party page can log a user out.
5. `SECRET_KEY` is hardcoded in `settings.py` and committed.
6. `DEBUG = True`, `ALLOWED_HOSTS = []`.
7. Raw Pinata error strings are returned to the browser verbatim.
8. Sumsub credentials are the literal strings `"your_secret_key"` / `"your_app_token"`.
9. No upload validation at all: any file type, any size, any count.
10. `django.contrib.auth` password validators are configured but the custom signup view
    bypasses them entirely by calling `User.objects.create_user` directly.

`manage.py check --deploy` reports 7 further warnings (HSTS, SSL redirect, secure
session and CSRF cookies, weak secret key, DEBUG, empty ALLOWED_HOSTS).

### 1.4 Correctness bugs

- **The wizard has zero validation.** `nextStep()` never calls `checkValidity()`, so the
  `required` attributes are decorative. Clicking Next on an empty step 1 creates a blank
  draft row. The database contains 8 such empty drafts, and the audit created a 9th.
  Compliance documents marked required can be skipped entirely.
- `submit_listing` re-uploads files from `request.FILES` rather than from the listing
  already saved in step 1 and step 3, so it double-uploads and depends on the browser
  still holding the files.
- The step title is set to `Step N of 5`, silently discarding the descriptive labels
  ("Upload Media", "Property Details") that exist in the markup.
- The preview builds HTML by string concatenation from user input into `innerHTML`.
- `openModal` adds `scale-100` while `scale-95` is still present, so the intended scale
  animation never runs. The fade is 500ms, which reads as lag.
- The nav renders a "Sign Up" button even when the user is already signed in.
- The mobile menu shows Login and Sign Up regardless of auth state, and links to
  Marketplace and Dashboard for logged-out users.
- Dashboard lives at `/dashboard/dashboard/` because both the project URLconf and the
  app URLconf prepend `dashboard/`.
- `kyc_verification.html` is orphaned: no view, no URL, and its JS calls
  `SumsubKYC.init("#sumsub-websdk")` against a container whose real id is
  `kyc-container`.
- `Listing.other_docs` is assigned in step 3 but no such field exists on the model.
- The `users` app is three empty stub files. `dashboard/models.py` is empty.
- No `.env` file exists, and `settings.py` calls `config('PINATA_API_KEY')` with no
  default, so a clean clone will not boot.
- The committed `venv/` points at `C:\Users\Rahul Gulati\...`, a path that does not exist
  on this machine. 8,300 files of virtualenv are tracked in git.
- Three `tests.py` files, all empty. No `requirements.txt`. No README.

### 1.5 Why it feels slow, and why it reloads on every navigation

This is the single most visible performance complaint, and the cause is concrete:

- `cdn.tailwindcss.com` ships a **CSS compiler as a ~400KB JavaScript bundle** and runs a
  full JIT compile in the browser on every single page load. Tailwind's own console
  warning fires twice per page. Nothing is cached between navigations because the work is
  recomputed, not fetched.
- Bootstrap Icons loads an entire icon **webfont** (~120KB) to render about 12 glyphs.
- Google Fonts is linked in `<head>` with no `preconnect` and no `preload`.
- Alpine.js is pulled from `unpkg.com/alpinejs` with no version pin.
- Card images are remote Unsplash URLs with no `width`/`height`, so every page reflows as
  they arrive.
- Django's dev static handling sends no far-future cache headers, and there is no
  `ManifestStaticFilesStorage`, so nothing is fingerprinted or cached hard.
- Every navigation is a full document load. There is no partial rendering anywhere.

### 1.6 Accessibility and SEO

Measured in-page: 4 images with no `alt`, 20+ form inputs with no associated `<label>`,
5 icon-only buttons with no accessible name, no `meta description`, no favicon, no
`og:` tags, no skip link, no focus-visible styling, no custom 404.

### 1.7 The largest gap

The marketing copy promises "AI-powered valuations", "MeTTa agents", "automated valuation
and risk assessment", and "NFT minting". **There is no AI code in the repository, and no
token or minting code.** The word "AI" appears only in prose.

---

## Part 2. Design direction

The redesign brief is a full overhaul of the visual language with the content and brand
preserved. Three anti-slop constraints drive the choices:

**Palette: Forest.** Deep green, bone, single amber accent. This keeps Terrax recognisably
green (the existing brand) while avoiding the two most common AI fingerprints: the
purple/blue gradient, and the beige-plus-brass "premium consumer" default. One accent,
locked across the whole page. Saturation held under 80%. Shadows tinted to the surface
hue, never pure black.

**Type: Geist + Geist Mono.** Inter is the current font and is replaced. All numerals,
token ids and hashes render in Geist Mono with `font-variant-numeric: tabular-nums`, so
price columns line up. Display sizes use tight tracking; body copy is capped at 65
characters.

**Layout: no repetition.** At least four different section families per page. No three
equal feature cards. No zigzag past two consecutive sections. Max one eyebrow label per
three sections. Hero fits the first viewport with the CTA visible.

**Banned outright** in every template: em-dashes, emoji in UI chrome, "Elevate",
"Seamless", "Unleash", "Next-Gen", section-number labels, "Stage 1 / Stage 2", fake
precise statistics, decorative status dots, scroll cues, div-based fake screenshots,
placeholder-as-label, `window.alert()`.

**Both themes.** Light and dark, `prefers-color-scheme` by default with a manual toggle,
locked per page so no section inverts mid-scroll. Motion honours
`prefers-reduced-motion` and animates only `transform` and `opacity`.

Icons come from Phosphor as a self-hosted SVG sprite containing only the glyphs used.

---

## Part 3. Architecture

Same stack: Django 5, Tailwind, Alpine. Nothing is migrated to a new framework. The apps
are reorganised so each has one job, and every template and view is rewritten from
scratch rather than patched.

```
terrax/          settings split into base / dev / prod, root urls
core/           base layout, design tokens, context processors, error pages, health check
accounts/       profile, KYC, wallet, notifications, saved searches, settings
properties/     Listing, media, documents, wizard, detail, search and filtering
market/         offers, negotiation, fractional shares, transactions, portfolio
intelligence/   valuation engine, risk scoring, document checks, LLM layer
chain/          IPFS pinning and token records (replaces marketplace/utils/ipfs.py)
```

`users/` (an empty stub) and `dashboard/` (one hardcoded template) are removed, their
responsibilities absorbed into `accounts/` and `core/`.

Code conventions, chosen because this repository is going to be read in interviews:
thin views, a `services.py` per app holding the business logic, Django Forms for all
input, type hints on every public function, `constants.py` instead of magic strings,
docstrings that explain why rather than what.

---

## Part 4. Work items

### 4.1 Fixes

Security. `@login_required` and `require_POST` on every mutating endpoint. Owner-scoped
querysets so cross-user access is impossible by construction. Logout becomes POST-only.
Settings split with `SECRET_KEY`, `DEBUG` and `ALLOWED_HOSTS` from the environment, plus
secure cookie flags and HSTS under prod. Upload validation for content type, size, count
and extension. Provider errors logged server-side and replaced with a safe message
client-side. Rate limiting on the AI endpoints. Django's password validators actually
enforced at signup.

Correctness. Every form becomes a Django `Form` or `ModelForm`, validated server-side.
The wizard refuses to advance a step that does not validate, and no draft row is created
until step 1 passes. `submit_listing` reads files from the saved listing. `type="button"`
on every non-submitting button. Public listing ids become UUIDs so they are not
enumerable. Templates render from the database only.

Data. Indexes on the columns actually filtered (`status`, `location`, `property_type`,
`price`, `created_at`). `select_related` and `prefetch_related` on every list query.
A `seed_demo` command that clears the junk drafts and loads realistic listings.

### 4.2 Performance

The fix for "it loads every time and takes forever":

1. Tailwind CLI build to a single hashed `app.css`. Removes ~400KB of JavaScript and the
   in-browser compile from every page load.
2. WhiteNoise with `CompressedManifestStaticFilesStorage`. Fingerprinted filenames,
   immutable cache headers, gzip and brotli.
3. Self-hosted Geist subset, preloaded, `font-display: swap`.
4. Phosphor SVG sprite with only the icons in use, replacing the icon webfont.
5. HTMX for search, filters, pagination and tab switching. The page shell stays mounted
   and only the results fragment re-renders, with `hx-push-url` keeping back/forward
   working.
6. Django cache framework (LocMem in dev, Redis-ready in prod) for market statistics and
   AI results.
7. AI outputs persisted on the model, computed once and refreshed on demand rather than
   recomputed per request.
8. Every image gets explicit `width`/`height`, `loading="lazy"`, `decoding="async"`, and a
   thumbnail generated at upload time.

Targets: LCP under 2.5s, CLS under 0.1, no layout shift on navigation.

### 4.3 Features that are missing

The current product has one action available to a user: list a property, once. That is
why it cannot support daily use. The new loop is browse, analyse, offer, negotiate, hold
fractions, track the portfolio, respond to alerts, repeat.

**Properties**
- Property detail page: gallery, map, specification, documents with verification status,
  valuation panel, ownership breakdown, price history, offer panel, similar listings.
- Real search and filtering: full-text, location, type, price range, bedrooms, size,
  status, sort, all over HTMX.
- Saved searches with alerts when a new listing matches.
- Watchlist.
- Compare up to four listings side by side. The dead "Compare" button becomes real.
- Map view with marker clustering, using Leaflet and OpenStreetMap so no API key is
  needed.
- Edit, delete, publish, unpublish, and resume an abandoned draft.
- A verification pipeline with honest states: draft, submitted, under review, verified,
  tokenized, listed, sold.

**Market**
- Offers, counter-offers, accept, reject, withdraw, with a negotiation thread per offer.
- Fractional ownership: buy and sell fractions, an order book per listing, a share ledger.
- Transaction history.
- Portfolio: holdings, cost basis, unrealised profit and loss, allocation by type and
  location, value over time.
- Price history per listing.
- Market pulse: index, movers, new listings, volume by location.

**Accounts**
- Profile with avatar, and a KYC flow with real review states, replacing the dead Sumsub
  stub.
- Wallet connect, disconnect, and ownership verification by signature.
- Notification centre and preferences.
- Activity feed.
- Public seller profile page.

**Content**
- About, How it works, Contact with a working form, Help centre, Guides, Privacy, Terms,
  and custom 404 and 500 pages. This retires all 16 dead links.

### 4.4 UX

Skip-to-content link. Visible focus rings. A label on every input. Alt text on every
image. Accessible names on icon buttons. Command palette on Ctrl/Cmd+K. Skeleton loaders
shaped like the content they replace, not spinners. Empty states that offer the next
action. Inline field errors, never `alert()`. A toast system for async results. Optimistic
UI on watchlist and offers. Breadcrumbs, active nav state, and a way back from every page.
Smooth scrolling. Dark mode toggle. On mobile: a bottom navigation bar, sheet dialogs, and
44px touch targets. An onboarding checklist for new accounts. A currency toggle, because
the current data mixes rupees and dollars in the same view.

### 4.5 AI, built properly

Two layers, so the site is fully functional with no API keys configured.

**Layer 1: deterministic engine.** Plain readable Python, no network calls.

- *Valuation by comparable sales.* Select comparables by type, location and a size band,
  weight them by similarity, take a weighted median, then apply explicit adjustments for
  size, age, bedrooms and amenities. Returns a point estimate, a confidence interval, and
  the list of comparables used, so the number can be defended.
- *Risk score.* Weighted factors: document completeness, KYC state, deviation from
  comparable prices, data completeness, listing age. The contributing factors are shown
  alongside the score.
- *Document checks.* Content-type sniffing, size and dimension checks, expiry-date
  parsing, checksum, and duplicate detection across listings.
- *Market statistics.* Rolling medians, price per square foot by location, momentum.

**Layer 2: LLM, optional.** A provider interface in `intelligence/providers/` with four
backends selected by environment variable, and a null provider that degrades to layer one:

| Provider | Model | Free tier |
| --- | --- | --- |
| Groq | `llama-3.3-70b-versatile` | 30 req/min, 1,000 req/day |
| Google Gemini | `gemini-2.5-flash` | free tier in AI Studio |
| OpenRouter | any `:free` model | 50 req/day |
| Ollama | local | unlimited, offline |

Features on top: a listing description writer that works from the structured facts, a
plain-language explanation of the deterministic valuation, document summaries with red
flags, natural-language search that compiles to filters, a property Q&A assistant grounded
strictly on that listing's own data, and a weekly market digest.

Engineering details that matter under review: JSON-schema-constrained output with
validation, retry with exponential backoff, a token budget guard, response caching keyed
by a hash of the input, graceful degradation to layer one on any failure, and every AI
output stamped with its provider, model and timestamp. A "how this was calculated" panel
exposes the comparables and weights to the user.

The existing Pinata keys and settings are not touched.

### 4.6 The daily-use loop

The dashboard opens on a feed: new matches from saved searches, offers received or
updated, price movement on watched listings, portfolio change since last visit,
verification status changes, and the market digest. No gamification, no fake streaks, no
invented numbers. The reason to return is that the state actually changed.

### 4.7 Code quality, for the interview

pytest suite covering models, forms, views, permissions, the AI fallback path, and an
end-to-end wizard run. `ruff` and `black` configured. `requirements.txt` and
`.env.example` committed, `venv/` and `db.sqlite3` untracked. A README with an
architecture diagram, setup instructions, and a section explaining the design decisions
and the trade-offs behind them. Management commands for seeding demo data and recomputing
valuations.

Every file that is replaced is deleted and rewritten, not extended. No commented-out
blocks, no dead code, no accretion.

---

## Part 5. Order of delivery

1. Foundation: settings split, dependencies, environment, static pipeline, design tokens,
   base layout, error pages.
2. Data: new apps, models, migrations, seed command.
3. Accounts: auth, profile, KYC, wallet, notifications.
4. Properties: index, search, detail, wizard, edit.
5. Intelligence: deterministic engine first, then the LLM layer on top.
6. Market: offers, fractions, portfolio.
7. Dashboard, feed, notifications.
8. Content pages, 404, SEO and social metadata.
9. Tests, README, and a final pass verifying both themes and mobile in the browser.
