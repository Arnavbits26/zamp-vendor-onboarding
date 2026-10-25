# Assumptions & design decisions

Kept deliberately short so it can be referenced live in the interview.

## Scope
1. **Indian domestic vendors only.** Identifiers are PAN, GSTIN and IFSC. Foreign vendors (VAT/EIN, SWIFT) are a natural extension: the validator layer is per-country by design.
2. **Vendors must be GST-registered.** Unregistered small vendors (below the GST threshold) are out of scope; in production they'd submit a non-registration declaration instead.
3. **No live government or bank APIs.** In production I'd add a GST portal lookup (is the GSTIN active?) and a penny-drop check (does the bank confirm the account holder's name?). Here the cancelled cheque is the bank evidence, and the vendor master stands in for the company's existing records.

## Required documents by entity type
| Entity type | GST cert | PAN card | Cancelled cheque | Incorporation cert |
|---|---|---|---|---|
| Private / public limited | yes | yes | yes | yes |
| LLP | yes | yes | yes | yes (LLP certificate) |
| Sole proprietorship | yes | yes (proprietor's) | yes | no |

## What each outcome means
- **APPROVED**: every required item is present, well-formed, and consistent across form and documents.
- **PENDING**: something is missing, unreadable or inconsistent in a way a genuine vendor could fix. The vendor gets a precise list of what to send.
- **REJECTED**: a contradiction that points to fraud or ineligibility (e.g. bank account already belongs to another vendor). Procurement gets the detailed reason; **the vendor gets a neutral "unable to proceed, our team will contact you" message.** Fraud checks are never explained to the submitter.

## Where AI is used, and where it isn't
- **LLM (Claude, vision):** classify each uploaded document ("is this actually a GST certificate?"), extract fields from it, and draft the vendor/internal messages.
- **Deterministic code:** every format check, every comparison, and the final decision. Same input gives the same verdict every time, and every verdict cites rule IDs.
- If extraction confidence is low or a field can't be read, the result is PENDING (ask for a clearer copy), never a guess.

## How documents are read
- A PDF with a machine-readable text layer (80+ characters) is sent to the model as **text**: cheaper and exact. A scanned PDF or a photo is sent as an **image**. The run log shows which route each document took.
- The model sees only the document itself: not the form data, the upload slot or the file name. It classifies the document first, so a PAN card uploaded in the GST slot is caught (`DOC-01`) rather than silently misread.
- The model copies fields exactly as printed and returns null for anything it can't see. Code then normalises identifiers (strips spaces, upper-cases). Anything unreadable becomes a PENDING ask for a clearer copy, never a guess.
- **Model choice:** the reader runs on Google's free Gemini tier by default (provider is a one-line `.env` setting; Claude is also supported). Every model reply is forced into a fixed shape in code (`coerce_extraction`): unknown document types become 'other', missing fields become null. Free tiers can rate-limit, so the reader retries with backoff and then fails with a clear message instead of crashing.
- **Privacy:** free-tier requests may be used by the provider to improve its products, so only fictitious specimen documents are ever sent. Real vendor documents would need a paid or enterprise tier.
- Document text is treated as untrusted data: instructions printed inside an uploaded file are ignored.

## Name matching
Names are compared after normalising case, punctuation and company suffixes ("Pvt Ltd" = "Private Limited"). A sole proprietor's bank account in the owner's personal name is valid **only** when the GST certificate links that person to the trade name.

## Resubmission
A resubmission keeps the same submission ID with a new version number. The whole pipeline re-runs (simplest way to guarantee nothing stale is trusted), and the UI highlights which checks changed between versions.

## Decision policy (the core judgement)
- **Missing or fixable information -> PENDING.** Typos, wrong or unreadable files, a state mismatch: a genuine vendor can fix these, so they get a precise list of what to send.
- **Contradictory identity information -> REJECTED.** Bank account already used by another vendor, GSTIN already onboarded, PAN belonging to an unrelated business, or three or more separate inconsistencies (each could be a typo; together they suggest the documents don't belong to one business).
- **Warnings never block.** Free email on a company, a small spelling difference, same PAN as an existing vendor with the same name (likely an extra state registration).
- **A check that can't run is SKIPPED, not failed.** If the GST certificate is the wrong file, everything that depends on it is skipped; the vendor is asked for that one document, not blamed for five things.
- **Sole proprietors may bank in their own name** only when the GST certificate ties the trade name to that person and the PAN is an individual's. A company never may.
- **Messages:** vendor asks are fixed, vendor-safe wording written by code. The model only writes a friendly greeting and sign-off for PENDING emails (with a template fallback). REJECTED emails are a fixed template: no model, no reasons.
- Name matching ignores case, punctuation, word order and company suffixes ("Pvt Ltd" = "Private Limited"); one-letter typos count as 'close' (a warning), anything else as different.

## Rule ID scheme
| Prefix | Stage | Examples |
|---|---|---|
| `CMP-` | Completeness | required fields, required documents for entity type |
| `FMT-` | Format | `FMT-PAN-01`, `FMT-GSTIN-01..03` (structure, state code, checksum), `FMT-IFSC-01`, `FMT-ACCT-01` |
| `DOC-` | Document reading | document type matches slot, fields readable |
| `XREF-` | Cross-reference | names agree, PAN inside GSTIN = declared PAN, GSTIN state = address state, PAN holder type = entity type |
| `RISK-` | Risk | `RISK-01` bank account on another vendor, `RISK-02` GSTIN already onboarded, `RISK-03` PAN on an unrelated vendor, `RISK-04` pattern of inconsistencies, `RISK-05` free email on a company |

## Test data
All entities, IDs and documents are fictitious and watermarked SPECIMEN. GSTINs are generated with the real checksum so they pass format validation.
