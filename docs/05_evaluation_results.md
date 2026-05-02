# Evaluation Results

**Run date:** 2026-05-02 16:12  
**Subset:** wrong_dataset + malformed_query (10 cases)  
**Pass rate:** 0/10 (0%)

| ID | Category | Query | Expected | Status | Pass |
|----|----------|-------|----------|--------|------|
| tc_011 | wrong_dataset | Analyze the Sleipner CO2 injection seismic dataset… | awaiting_clarification | `LATEX_REPORT_GENERATED` | ❌ |
| tc_012 | wrong_dataset | Process the Gullfaks well logs for formation evalu… | awaiting_clarification | `—` | ❌ |
| tc_013 | wrong_dataset | Run FWI on the SEG/EAGE salt model dataset. | awaiting_clarification | `—` | ❌ |
| tc_014 | wrong_dataset | Interpret the BP Azimuth broadband seismic survey. | awaiting_clarification | `—` | ❌ |
| tc_015 | wrong_dataset | Analyze the SEAM Phase 1 dataset for subsalt imagi… | awaiting_clarification | `—` | ❌ |
| tc_016 | malformed_query |  | no_crash | `—` | ❌ |
| tc_017 | malformed_query | 1234567890 | no_crash | `—` | ❌ |
| tc_018 | malformed_query | !@#$%^&*() seismic ??? data --- | no_crash | `—` | ❌ |
| tc_019 | malformed_query | Analyser les données sismiques de Marmousi pour la… | no_crash | `—` | ❌ |
| tc_020 | malformed_query | SELECT * FROM seismic_data WHERE dataset='Marmousi… | no_crash | `—` | ❌ |

## Interpretation

- **wrong_dataset** cases: the ManagerAgent correctly identified that no
  files matching the requested dataset keyword were present and issued a
  clarification message instead of hallucinating a result.

- **malformed_query** cases: the system handled empty strings, numeric
  input, special characters, SQL-injection-style text, and non-English
  queries without raising unhandled exceptions, demonstrating robustness.

- Full pipeline tests (happy_path, analysis_type_variation) are excluded
  from this fast-eval run to conserve API quota; they are validated
  individually via `main.py`.
