# M53.1-R — Response Reuse Provenance Recovery

## Scope and zero-call accounting

This recovery made zero provider/model calls, retries, repairs, judges, or selectors. It reconstructed the retained M51B request builder against exact archived pre- and post-M53 source commits.

Historical M51B corpus: `9783930bb0dac022a926c6638822e56747630f3605910cbf8487d409c5105d9a`. M53.1 fresh corpus: `1bb22e9c70455d7710fc5990f8b9af0dc54090bc6f83ab182f9113cb22647ab4`. Both byte hashes were verified.

## Exact request reconstruction

The canonical builder is `benchmark.m51b_runner._requests`, using `serialize_governed_context_v1`. The builder, system prompt, and response schema are unchanged between the source commits. Fingerprints include case ID, question, complete governed context, model configuration, messages, and strict response schema. Request IDs, trace IDs, provider metadata, timestamps, and timeout are transport-only exclusions.

Exact rendered provider requests changed for **80/90** cases. Hash-only/canonicalization mismatches: **0**.

## The 11 M53.1 mismatches

| Case | Pre visible hash | Post visible hash | Components | Origin | Reuse |
|---|---|---|---|---|---|
| `healthcare_02` | `80c5626aa5c654803e7ea3ab845727b26e640ee9a5e1e860c018f0b4c1e6ef76` | `81256e210bbd7545ddc3efcf112dc76f6462756fa4f640a59a3ea99942126d9b` | attributes, business_rules | SHARED_DOMAIN_CONTEXT_CHANGE | INVALIDATED |
| `healthcare_05` | `1971ce1082ea502ec088ffcfeb5d8754d4c6426b5ea719f92e1f8a90aaeee77b` | `525184ff48b3240eea50aa98b1979e7cf30743fe9540d23cad91ca53200dac48` | attributes, business_rules | SHARED_DOMAIN_CONTEXT_CHANGE | INVALIDATED |
| `healthcare_06` | `886aff8e28f3bec8d81565866db76e6e90f29cdb2cbf330351348eb7165416a8` | `5325bfc000b8f054dbff7a087ba561e3dc48b3347a13fcad5db2519d4ecac860` | attributes, business_rules | SHARED_DOMAIN_CONTEXT_CHANGE | INVALIDATED |
| `insurance_04` | `6be50f2d0d36e1bce83a41671ba83ccdaded369ecc50dcdd2d1f56a73bd6d984` | `863735faea7f2b38cc8ad81485dc0f716ef35e1bde4d8dd696b08e279f9277d4` | attributes | SHARED_DOMAIN_CONTEXT_CHANGE | INVALIDATED |
| `procurement_02` | `52d76c12485d96f1f6e34f6e2e18dac4794eaefcb1ffe91fefde6b3995869f31` | `74410b99b1516328273254de6cd95e24c15e30817c3d519cc2486e69fb25b13c` | attributes | SHARED_DOMAIN_CONTEXT_CHANGE | INVALIDATED |
| `procurement_05` | `c7cb194121d54783d373941773830d0876316a854805c10f0bbd4813984bd083` | `97db031687930eee897c7cf35dd95517c39bb89a49c26564259ee669dff03c9a` | attributes | SHARED_DOMAIN_CONTEXT_CHANGE | INVALIDATED |
| `procurement_08` | `2085a5514a8b90e27ad3ca570d47490f61a13119919cdb9985fe6f765baafaae` | `b60d63aaf842798fd938e8a17fdd89e0715ff6b4d3a2d8193b54077098e168b1` | attributes | SHARED_DOMAIN_CONTEXT_CHANGE | INVALIDATED |
| `procurement_11` | `09432eb3aa36c721c3ff8540e6517efc352e0df6805c726e007a94f5e840ec01` | `ba9c3633b22d1a7a144e4c08022c14b255739fc5be08503a63ed799fdc98a19d` | attributes | SHARED_DOMAIN_CONTEXT_CHANGE | INVALIDATED |
| `telecom_02` | `c45dd99a0ea42322ef1ba257886948d0348880fcca0a5e4a4e596c881059076a` | `2c4c10f60235a46c4c752d8d7a179e2617e3889e7334b259ac027b9b979beb7e` | attributes, business_rules | SHARED_DOMAIN_CONTEXT_CHANGE | INVALIDATED |
| `telecom_05` | `0d6f17946ce23f27b7c10d661c8c5cd5970e20a1268874d6193bd9c4f184ee7d` | `a0e52c7f4c503a9a47ef469a08aa520ede9b2519e1d040e544fb61d5a62ae7f1` | attributes, business_rules | SHARED_DOMAIN_CONTEXT_CHANGE | INVALIDATED |
| `telecom_08` | `18a64af4e3a67f07788af668efc0fd26a14e69018d608a8588860a2b74b2d602` | `75dc9467befa9242f7f26cd1a856a4f0b963cd5ff7cfe1411055b922ac5fe55b` | attributes, business_rules | SHARED_DOMAIN_CONTEXT_CHANGE | INVALIDATED |

All 11 questions were unchanged; each changed because shared model-visible authority/catalog context changed. M53’s persisted reusable status therefore did not represent exact provider-visible equality.

## Shared-context propagation

The changed domain artifacts affected all 15 rendered requests in each of five domains: procurement, insurance, telecom, healthcare, and workforce. In total, 75 cases changed through shared context. Direct case-question changes affected 18 cases. Five marketplace cases changed directly without shared context. Prompt/system and response-schema changes were zero.

## Corrected response availability

Exact pre/post request equality yields **10/90** reusable M51B responses. All **24/24** M53.1 fresh responses match their current post-M53 requests. **56/90** cases have no valid current-input response.

Partition: `A_REUSABLE_M51B_RESPONSE=10`, `B_VALID_M531_FRESH_RESPONSE=24`, `C_NO_VALID_CURRENT_RESPONSE=56`; total 90.

The missing cases and deterministic future schedule are frozen in `m531r_missing_current_response_schedule.json`. No score is computed because the current response corpus is incomplete.

## Hash/lineage conclusion

The M53 hash function included governed context, but its persisted ledger is not reproducible from either exact source commit: 0/90 persisted ledger hashes matched reconstructed pre-M53 visible hashes and 0/90 matched reconstructed post-M53 visible hashes. The recovery conclusion is `REUSABILITY_CLASSIFICATION_DEFECT` plus `SHARED_CONTEXT_PROPAGATION_DEFECT`; M53.1’s use of a semantic-content hash instead of a complete request fingerprint is also recorded as a recovery limitation.

## Preservation and next action

No benchmark, truth, reference, fixture, prompt, runtime, or response bytes were modified. M53 and M53.1 historical verdicts remain unchanged. The exact future fresh-call budget is the missing count above. M53.2 is not ready.

Final verdict: `M531R_PROVENANCE_RECOVERY_COMPLETE`.
