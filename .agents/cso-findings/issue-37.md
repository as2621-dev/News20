# CSO findings — issue #37 (voice connect UX + latency marks)

## Checked (pass)
- Latency marks log durations/booleans only; a test asserts no `auth_tokens` value ever appears in a mark line.
- The pre-warmed token lives only in a React ref (memory); never localStorage/sessionStorage.
- Token-mint endpoint auth boundary unchanged (same POST path/base-URL logic, extracted verbatim).
- Mint response shape validated before use (missing `ephemeral_token_name` → loud throw).

## Low (deferred)
- **Unauthenticated pre-warm mint on sheet-open.** `prewarmToken()` fires a mint the moment the
  voice sheet opens, even on the permission CTA — each sheet open can consume one single-use
  ephemeral token that may never be used (user taps NOT NOW). The endpoint was already
  unauthenticated + rate-limited pre-#37; this only raises call volume per user session
  (bounded: one per sheet-open, deduped while cached). If worker-side mint volume becomes a
  concern, gate the pre-warm on `blip-voice-granted` or add per-IP mint budgets on the worker.
