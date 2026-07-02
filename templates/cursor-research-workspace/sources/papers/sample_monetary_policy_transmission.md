# Sample Literature Note: Monetary Policy Transmission

This is a synthetic, redacted sample note for testing `R-LITERATURE` behavior. It is not a real paper and must not be cited as external evidence.

## Research Question

How can tighter monetary policy affect bank credit supply?

## Data And Method

The hypothetical study compares banks with different pre-existing capital buffers before and after a policy tightening episode.

## Identification Strategy

The intended identification strategy is a difference-in-differences design:

- treatment group: banks with lower capital buffers before tightening;
- comparison group: banks with higher capital buffers before tightening;
- outcome: loan growth to similar borrowers after tightening;
- key assumption: absent the tightening shock, treated and comparison banks would have followed parallel lending trends.

## Main Finding

The sample finding is that banks with lower capital buffers reduce loan growth more after tightening. This supports a bank balance-sheet channel interpretation, but only within the toy assumptions of this sample note.

## Limitations

- Parallel trends must be tested, not assumed.
- Borrower demand and bank supply may be hard to separate.
- The sample contains no real dataset, dates, policy documents, or external citations.

## How To Use This File

Use this file only to smoke-test local-only literature reading. A correct `R-LITERATURE` response should summarize the note, discuss the identification strategy and limitations, and avoid calling `ir_search` unless the user asks for current verification or external corroboration.
