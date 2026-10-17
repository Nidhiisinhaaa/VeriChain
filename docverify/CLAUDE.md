# VeriChain — Blockchain-Based Document Authentication and Verification System

## What this is
AI-powered document forgery detection + Ethereum blockchain notarization.
Institutions register documents (certificates, marksheets, legal docs) —
only the SHA-256 hash goes on-chain, never the file itself. Anyone can
verify authenticity by re-uploading a document or pasting its hash.

## Tech stack (fixed — don't substitute or add frameworks)
- Backend: Django 4.2, SQLite
- Blockchain: Solidity ^0.8.19, Ganache (local dev chain), Web3.py, py-solc-x
- OCR: pytesseract (Tesseract), pdf2image, Pillow
- Forgery detection: OpenCV + NumPy (ELA, EXIF checks, ORB copy-move detection)
- Frontend: Django templates + vanilla HTML/CSS/JS — no React/Bootstrap
- Auth: Django's built-in User model only — no JWT/OAuth

## Project structure
```
docverify/
├── contracts/
│   ├── DocumentRegistry.sol      # DONE — registerDocument/verifyDocument/
│   │                              #   revokeDocument/issuer auth, events
│   └── build/                    # ABI + bytecode land here after deploy script
├── docverify/                    # Django project core — settings/urls/wsgi/asgi (DONE)
├── verification/                 # main app
│   ├── models.py                 # DONE — Document, VerificationLog
│   ├── ocr_utils.py              # DONE — compute_sha256, extract_text, extract_key_fields
│   ├── forgery_detection.py      # TODO — Phase 1
│   ├── blockchain_utils.py       # TODO — Phase 2
│   ├── forms.py / views.py       # TODO — Phase 4
│   ├── templates/verification/   # TODO — Phase 5
│   └── static/verification/      # TODO — Phase 5
├── scripts/deploy_contract.py    # TODO — Phase 3
└── requirements.txt              # DONE
```

## Build order (see full spec in the original prompt / commit messages)
1. `verification/forgery_detection.py` — `analyze_document(path) -> dict`
   with `{score, flags, is_suspected}`. ELA + EXIF mismatch + ORB copy-move.
2. `verification/blockchain_utils.py` — web3.py wrapper: get_web3(),
   get_contract(), register_document_onchain(), verify_document_onchain(),
   revoke_document_onchain(). Friendly errors when Ganache isn't running.
3. `scripts/deploy_contract.py` — compile with py-solc-x (solc 0.8.19),
   write ABI/bytecode to contracts/build/, deploy to Ganache, print address.
4. `verification/forms.py` + `views.py` + `urls.py` — register_document,
   verify_document, document_detail, dashboard, home.
5. Templates + `static/verification/css/style.css` — clean/institutional
   tone, not flashy. Tabs for verify-by-upload vs verify-by-hash.
6. `admin.py`, migrations, `README.md`, `.env.example`.

## Conventions
- SQLite only — no Postgres/Docker.
- Don't hardcode chain=1337 outside the default config value; read from
  settings (WEB3_PROVIDER_URI / CHAIN_ID).
- Comment non-obvious logic (ELA thresholds, tx signing) — this ships in a
  project marketplace listing that buyers read.
- Match existing code style in models.py/ocr_utils.py/settings.py exactly.
- Every blockchain-writing view must degrade gracefully if Ganache is down.

## Definition of done
`python manage.py runserver` works end-to-end: issuer registers a
certificate (forgery check shown pre-registration) → lands on local
Ganache → anyone verifies by re-upload or hash paste → a tampered copy
of the same file correctly fails verification.

## Commands
- `python manage.py makemigrations verification && python manage.py migrate`
- `python scripts/deploy_contract.py` (Ganache must be running on
  `http://127.0.0.1:7545` first)
- `python manage.py runserver`
