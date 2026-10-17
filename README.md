# VeriChain

A document authentication and verification platform that combines AI-based
forgery detection with Ethereum blockchain notarization. Institutions
register documents (academic certificates, marksheets, legal documents) as
tamper-proof records; anyone can verify a document's authenticity instantly
by re-uploading it or pasting its hash.

**Only a document's SHA-256 hash is ever stored on-chain — never the
document itself.**

## Architecture

```
                         ┌────────────────────────┐
                         │        Browser          │
                         │  (Django templates,     │
                         │   vanilla HTML/CSS/JS)  │
                         └───────────┬─────────────┘
                                     │ HTTP
                         ┌───────────▼─────────────┐
                         │      Django app          │
                         │  verification/views.py   │
                         └───┬───────┬───────┬──────┘
                             │       │       │
              ┌──────────────┘       │       └───────────────┐
              ▼                      ▼                        ▼
   ┌─────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
   │   ocr_utils.py        │ │ forgery_detection.py  │ │  blockchain_utils.py  │
   │ Tesseract OCR +       │ │ ELA + EXIF checks +   │ │ web3.py: sign & send   │
   │ SHA-256 hashing        │ │ ORB copy-move          │ │ tx to DocumentRegistry │
   └─────────────────────┘ └──────────────────────┘ └───────────┬──────────┘
                                                                  │ JSON-RPC
                                                       ┌──────────▼──────────┐
                                                       │  Ganache (local      │
                                                       │  EVM dev chain)      │
                                                       │  DocumentRegistry.sol│
                                                       └──────────────────────┘

              ┌──────────────────────┐
              │      SQLite            │  <- Document, VerificationLog
              │  (db.sqlite3)          │     (local cache/audit trail;
              └──────────────────────┘     the chain is the source of truth
                                            for validity/revocation)
```

The document *file* is stored locally under `media/` (and referenced from
SQLite); the blockchain only ever receives the SHA-256 hash, the document
type, and an optional metadata string (e.g. the document's title). Anyone
with a copy of the file (or just its hash) can independently confirm it's
registered and unrevoked, without ever needing access to the original file.

## How verification works (plain-English version)

1. **A university registers a certificate.** VeriChain reads the text off
   it (OCR), runs some sanity checks for signs of digital tampering, then
   computes a "fingerprint" of the file — a SHA-256 hash. That fingerprint,
   not the file, gets written to the blockchain, permanently and
   publicly-auditably, alongside who registered it and when.
2. **Someone wants to check a certificate is real.** They upload the same
   file to VeriChain (or paste its fingerprint, if they already have it).
   VeriChain re-computes the fingerprint the same way.
3. **The fingerprints are compared.** A cryptographic hash is extremely
   sensitive to change — flipping a single character in the document
   produces a completely different fingerprint. So:
   - If the fingerprints match a blockchain record → the document is
     provably the exact same file the institution registered. ✅ Verified.
   - If they don't match anything → either it was never registered, or it's
     been altered since. ❌ Not Found (and if our AI forgery check also
     spots signs of editing, 🚨 Possibly Tampered).
   - If they match a record the issuer has since revoked (e.g. a cancelled
     certificate) → ⚠️ Revoked.

## Tech stack

- **Backend:** Django 4.2, SQLite
- **Blockchain:** Solidity ^0.8.19, Ganache (local dev chain), Web3.py, py-solc-x
- **OCR:** Tesseract (pytesseract), pdf2image, Pillow
- **Forgery detection:** OpenCV + NumPy — Error Level Analysis, EXIF
  metadata checks, ORB-based copy-move (clone) detection
- **Frontend:** Django templates, vanilla HTML/CSS/JS (no framework)
- **Auth:** Django's built-in `User` model — `is_staff` accounts are
  issuers (can register/revoke documents); everyone else can only verify

## Setup

### 1. System dependencies

- **Python 3.10+**
- **Tesseract OCR** — required for text extraction
  - Windows: install from https://github.com/UB-Mannheim/tesseract/wiki,
    then set `TESSERACT_CMD` in `.env` to the installed `tesseract.exe` path
  - macOS: `brew install tesseract`
  - Linux: `sudo apt install tesseract-ocr`
- **Poppler** — required by `pdf2image` for PDF support
  - Windows: download from https://github.com/oschwartz10612/poppler-windows/releases,
    add its `bin/` folder to your `PATH`
  - macOS: `brew install poppler`
  - Linux: `sudo apt install poppler-utils`
- **Ganache** — local Ethereum dev chain. Either:
  - the [Ganache GUI app](https://trufflesuite.com/ganache/) (default RPC
    `http://127.0.0.1:7545`), or
  - `npx ganache --port 7545` (Node.js required)

### 2. Python environment

```bash
cd docverify
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

You'll fill in `CONTRACT_ADDRESS` and `DEPLOYER_PRIVATE_KEY` in the next
step — leave them blank for now.

### 4. Start Ganache, then compile & deploy the contract

Start Ganache first (GUI app, or `npx ganache --port 7545` in a separate
terminal), then:

```bash
python scripts/deploy_contract.py
```

This compiles `contracts/DocumentRegistry.sol` with solc 0.8.19 (downloaded
automatically on first run), writes the ABI/bytecode to `contracts/build/`,
and deploys it using Ganache's first pre-funded account. Copy the printed
`CONTRACT_ADDRESS` into `.env`, and copy that same account's private key
(visible in the Ganache GUI's Accounts tab, or printed by the CLI on
startup) into `DEPLOYER_PRIVATE_KEY`.

### 5. Migrate the database & create an issuer account

```bash
python manage.py migrate
python manage.py createsuperuser
```

The superuser account is automatically an issuer (`is_staff=True`), so it
can register documents. To make additional issuer accounts without full
admin rights, create a regular user from `/admin/` and tick "Staff status".

### 6. Run it

```bash
python manage.py runserver
```

Visit `http://127.0.0.1:8000/`. Log in with your superuser account to
register documents at `/register/`; verification at `/verify/` needs no
login.

## Project layout

```
docverify/
├── contracts/
│   ├── DocumentRegistry.sol      # the on-chain registry contract
│   └── build/                    # ABI + bytecode (generated by deploy_contract.py)
├── docverify/                    # Django project settings/urls/wsgi/asgi
├── verification/                 # the app
│   ├── models.py                 # Document, VerificationLog
│   ├── ocr_utils.py              # SHA-256 hashing, OCR text extraction
│   ├── forgery_detection.py      # ELA + EXIF + copy-move analysis
│   ├── blockchain_utils.py       # web3.py wrapper around the contract
│   ├── forms.py / views.py / urls.py
│   ├── templates/verification/
│   └── static/verification/{css,js}/
├── scripts/deploy_contract.py    # compile + deploy DocumentRegistry.sol
├── requirements.txt
└── .env.example
```

## Notes & known limitations

- **Single custodial signing wallet.** All on-chain writes (register/
  revoke) are signed with `DEPLOYER_PRIVATE_KEY` on behalf of whichever
  Django user is logged in — there's no per-user wallet/MetaMask flow.
  This keeps the demo free of wallet-connect complexity; every
  registration is still a real, independently-verifiable transaction.
- **Forgery detection is a heuristic, not a verdict.** ELA, EXIF checks and
  copy-move detection each have false-positive and false-negative modes
  (e.g. a low-quality scan can trip ELA; a photo from a phone with no EXIF
  isn't necessarily edited). The score is shown to the issuer so *they*
  make the final call, with a required confirmation step if it crosses the
  suspicion threshold.
- **Graceful degradation.** Every blockchain call is wrapped with friendly
  error handling — if Ganache isn't running, you'll see a clear in-app
  message telling you to start it (or re-run the deploy script), not a
  stack trace. A registration that fails because the chain was down can be
  retried from the document's detail page once it's back up.
- **`py-solc-x` version.** `requirements.txt` pins `py-solc-x==2.0.5`
  rather than an earlier 2.0.x release — versions before 2.0.3 point at
  `solc-bin.ethereum.org`, a domain the Solidity team has since
  decommissioned in favor of `binaries.soliditylang.org`, which makes
  `install_solc()` fail with a DNS error. 2.0.5 fixes this.
