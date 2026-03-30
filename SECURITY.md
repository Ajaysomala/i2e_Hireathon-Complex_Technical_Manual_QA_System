# Security Policy

## Overview

This project implements multiple layers of security across both the backend (FastAPI) and frontend (Vanilla JS). Below is a full breakdown of every security measure implemented and why.

---

## 🔒 Implemented Security Measures

### Backend — FastAPI (`main.py`)

#### 1. Rate Limiting (Per IP, Sliding Window)
```python
RATE_LIMIT_REQUESTS = 20    # max requests
RATE_LIMIT_WINDOW   = 60    # per 60 seconds
```
- Every `/ask` and `/ingest` request is checked against a per-IP sliding window
- Exceeding the limit returns HTTP `429 Too Many Requests`
- Prevents abuse, API key exhaustion, and denial-of-service on the Groq endpoint
- Implementation: in-memory `defaultdict` — no Redis dependency for local demo

#### 2. Input Length Validation
```python
MAX_QUESTION_LENGTH = 1000   # characters
MIN_QUESTION_LENGTH = 3      # characters
```
- Questions shorter than 3 chars or longer than 1000 chars are rejected with HTTP `400`
- Prevents prompt injection via oversized payloads
- Matches the `maxlength="1000"` attribute on the frontend textarea (belt-and-suspenders)

#### 3. Input Sanitization
```python
def sanitize_text(text: str) -> str:
    return re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', text)
```
- Strips null bytes and dangerous control characters before the text reaches the LLM
- Preserves legitimate `\n` and `\t` characters
- Prevents null byte injection and control character exploits

#### 4. Security Response Headers Middleware
Applied to **every response** via `SecurityHeadersMiddleware`:

| Header | Value | Purpose |
|--------|-------|---------|
| `X-Frame-Options` | `DENY` | Prevents clickjacking via iframes |
| `X-Content-Type-Options` | `nosniff` | Prevents MIME-type sniffing |
| `X-XSS-Protection` | `1; mode=block` | Legacy XSS filter for older browsers |
| `Referrer-Policy` | `no-referrer` | No referrer data sent on outbound requests |
| `Permissions-Policy` | `geolocation=(), camera=(), microphone=()` | Blocks unnecessary browser APIs |

#### 5. CORS Tightened to Localhost Only
```python
allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"]
```
- Changed from `allow_origins=["*"]` to localhost-only
- Prevents cross-origin requests from external domains
- Only same-origin frontend can call the API

#### 6. Trusted Host Middleware
```python
TrustedHostMiddleware(allowed_hosts=["localhost", "127.0.0.1", ...])
```
- Rejects requests with unexpected `Host` headers
- Prevents host header injection attacks

#### 7. Shutdown Endpoint Token Protection
```python
SHUTDOWN_TOKEN = secrets.token_hex(16)
```
- `/shutdown` endpoint requires a cryptographically random token generated at startup
- Token is printed to server console — only someone with local server access can shut it down
- Uses `secrets.compare_digest()` to prevent timing attacks
- Prevents accidental or malicious remote server termination

---

### Frontend — (`index.html`, `app.js`, `style.css`)

#### 8. Content Security Policy (CSP) Meta Tag
```html
<meta http-equiv="Content-Security-Policy"
      content="default-src 'self';
               script-src 'self';
               style-src 'self' https://fonts.googleapis.com;
               font-src 'self' https://fonts.gstatic.com;
               connect-src 'self' http://localhost:8000;
               img-src 'self' data:;
               frame-ancestors 'none';" />
```
- Blocks all inline scripts — prevents XSS via injected `<script>` tags
- Only allows connections to `localhost:8000` — no data exfiltration
- Blocks the page from being embedded in iframes anywhere (`frame-ancestors 'none'`)
- Allows only Google Fonts CDN for external resources

#### 9. XSS Prevention — Strict HTML Escaping
```javascript
function escapeHtml(str) {
    return String(str)
        .replace(/&/g,  "&amp;")
        .replace(/</g,  "&lt;")
        .replace(/>/g,  "&gt;")
        .replace(/"/g,  "&quot;")
        .replace(/'/g,  "&#x27;")
        .replace(/\//g, "&#x2F;");
}
```
- Applied to **all user input** before rendering in the DOM
- Applied to **all API responses** (AI answers, source titles, previews)
- Escapes 6 characters including `'` and `/` which many implementations miss
- Prevents stored and reflected XSS from both user queries and LLM responses

#### 10. Frontend Rate Limiting
```javascript
const RATE_LIMIT  = 3;       // max queries
const RATE_WINDOW = 10000;   // per 10 seconds
```
- Client-side sliding window prevents rapid-fire requests before they hit the backend
- Shows a user-facing error message with countdown: `"Wait Xs before next query"`
- Complements (does not replace) backend rate limiting

#### 11. Input Length Enforcement (Dual Layer)
- HTML: `maxlength="1000"` attribute on textarea
- JS: `sanitizeInput()` hard-slices at 1000 chars as fallback
- Visual: character counter turns amber at 700, red at 900
- Strips control characters client-side before sending to API

#### 12. Request Timeout
```javascript
const controller = new AbortController();
const timeoutId  = setTimeout(() => controller.abort(), 30000);
```
- All API requests abort after 30 seconds
- Prevents the UI from hanging indefinitely on slow responses
- Shows user-friendly timeout message

#### 13. Additional Meta Security Tags
```html
<meta http-equiv="X-XSS-Protection"      content="1; mode=block" />
<meta http-equiv="X-Content-Type-Options" content="nosniff" />
<meta http-equiv="Referrer-Policy"        content="no-referrer" />
<meta name="robots"                       content="noindex, nofollow" />
```
- `noindex, nofollow` — prevents local demo from being indexed by search engines
- Redundant with backend headers for defense-in-depth

---

## 🛡️ Security Architecture Summary

```
┌─────────────────────────────────────────────────────┐
│                    BROWSER                          │
│  CSP meta tag — blocks inline scripts & iframes    │
│  escapeHtml() — all DOM insertions sanitized        │
│  Frontend rate limit — 3 req / 10s                 │
│  Input maxlength + sanitization                     │
│  30s request timeout                                │
└──────────────────┬──────────────────────────────────┘
                   │ HTTPS / localhost
┌──────────────────▼──────────────────────────────────┐
│                 FASTAPI SERVER                      │
│  SecurityHeadersMiddleware — every response         │
│  TrustedHostMiddleware — host header validation     │
│  CORS — localhost only                              │
│  Rate limiting — 20 req / 60s per IP               │
│  Input validation — 3-1000 chars                   │
│  Input sanitization — control char stripping        │
│  Shutdown token — secrets.token_hex(16)             │
└──────────────────┬──────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────┐
│                 GROQ API                            │
│  API key stored in .env (never in code)             │
│  .env in .gitignore (never committed)               │
│  .env.example provided for setup guidance           │
└─────────────────────────────────────────────────────┘
```

---

## 🔑 API Key Security

- `GROQ_API_KEY` is loaded exclusively from `.env` via `python-dotenv`
- `.env` is listed in `.gitignore` — never committed to version control
- `.env.example` is provided showing required keys without values
- No API keys are hardcoded anywhere in the codebase

---

## ⚠️ Known Limitations (Local Demo Context)

| Limitation | Reason | Production Fix |
|------------|--------|----------------|
| In-memory rate limiting | No Redis dependency for local demo | Replace with `slowapi` + Redis |
| No HTTPS | Local execution only | Nginx reverse proxy + Let's Encrypt |
| No authentication | Single-user local tool | JWT tokens + user sessions |
| Shutdown token in console | Demo convenience | Environment variable injection |

---

## 📋 Reporting a Vulnerability

This is a local demo project built for i2e Hireathon 2026. It is not intended for public deployment.

If you find a security issue for educational purposes, feel free to open an issue or contact:

**Somala Ajay** — jaydeveloper010@gmail.com

---

*Security implementation by Somala Ajay — i2e Hireathon 2026*
