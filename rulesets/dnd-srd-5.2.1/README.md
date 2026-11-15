# Full SRD spell implementation in progress

This continues the Full development work restored by PR #1. The newer FC-14 source inventory labels 245 of 339 spells implemented and 94 pending, compared with the restored baseline's 59/280. These are source-inventory labels, not a claim that all behavior of those spells is complete or qualified against current main.

The Full ruleset files, owning generators and SRD regression sources are retained here for continuing the implementation. Generators and tests remain in the `srd_full` namespaces; imports and repository-root calculations were adjusted for those namespaces. Alpha defaults and Lite registration are unchanged.

Remaining work: reconcile this source with current runtime contracts, complete the pending spells and unmapped effects, bind required historical fixtures, regenerate the package, run owning regressions and qualify representative gameplay. Use the existing Full development materializer and opt-in legacy test switch documented in `tests/srd_full/conftest.py`. Historical tests are development inputs, not current passing evidence.
