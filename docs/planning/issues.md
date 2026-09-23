# Roadmap issues

Tracking issue: [Roadmap: native AI Key and AI Port parity plus an open-source product](https://github.com/Olli0103/unifi-ai-key-emulator/issues/1).

All entries are planned work. Follow issue dependencies and acceptance checks.

## 0. Evidence, security and feasibility

| Issue | Prerequisites |
| --- | --- |
| [#2 Define versioned AI Key parity contracts and native acceptance fixtures](https://github.com/Olli0103/unifi-ai-key-emulator/issues/2) | Ready to start |
| [#3 Establish security boundaries and adversarial tests before exposing the control site](https://github.com/Olli0103/unifi-ai-key-emulator/issues/3) | Ready to start |
| [#5 Define media, audio and recognition data retention and privacy controls](https://github.com/Olli0103/unifi-ai-key-emulator/issues/5) | [#3](https://github.com/Olli0103/unifi-ai-key-emulator/issues/3) |
| [#7 Define detection-quality, retrieval and security acceptance benchmarks](https://github.com/Olli0103/unifi-ai-key-emulator/issues/7) | [#2](https://github.com/Olli0103/unifi-ai-key-emulator/issues/2), [#3](https://github.com/Olli0103/unifi-ai-key-emulator/issues/3) |
| [#6 Define and prove a native AI Port compatibility contract](https://github.com/Olli0103/unifi-ai-key-emulator/issues/6) | [#2](https://github.com/Olli0103/unifi-ai-key-emulator/issues/2) |
| [#10 Verify which native basic and deep search contracts Protect actually uses](https://github.com/Olli0103/unifi-ai-key-emulator/issues/10) | [#2](https://github.com/Olli0103/unifi-ai-key-emulator/issues/2) |

The [AI Port controller contract](../evidence/ai-port-controller-contract.md) and [firmware/discovery evidence](../evidence/ai-port-firmware-contract.md) separate static interfaces from the native 7.3.60 candidate result. Adoption and event ingress remain open for #6.

## 1. All-camera foundation and control site

| Issue | Prerequisites |
| --- | --- |
| [#8 Create role-specific provider and model configuration with safe model discovery](https://github.com/Olli0103/unifi-ai-key-emulator/issues/8) | [#3](https://github.com/Olli0103/unifi-ai-key-emulator/issues/3) |
| [#9 Automatically discover all Protect cameras and expose feature eligibility](https://github.com/Olli0103/unifi-ai-key-emulator/issues/9) | [#2](https://github.com/Olli0103/unifi-ai-key-emulator/issues/2), [#3](https://github.com/Olli0103/unifi-ai-key-emulator/issues/3) |
| [#12 Implement durable all-camera processing with a global rolling 12-caption budget](https://github.com/Olli0103/unifi-ai-key-emulator/issues/12) | [#9](https://github.com/Olli0103/unifi-ai-key-emulator/issues/9) |
| [#13 Build authenticated administration APIs and transactional configuration updates](https://github.com/Olli0103/unifi-ai-key-emulator/issues/13) | [#3](https://github.com/Olli0103/unifi-ai-key-emulator/issues/3), [#8](https://github.com/Olli0103/unifi-ai-key-emulator/issues/8), [#5](https://github.com/Olli0103/unifi-ai-key-emulator/issues/5) |
| [#17 Build the local control site for providers, models, cameras and maintenance](https://github.com/Olli0103/unifi-ai-key-emulator/issues/17) | [#13](https://github.com/Olli0103/unifi-ai-key-emulator/issues/13), [#9](https://github.com/Olli0103/unifi-ai-key-emulator/issues/9), [#12](https://github.com/Olli0103/unifi-ai-key-emulator/issues/12) |

## 2. Native detections and search

| Issue | Prerequisites |
| --- | --- |
| [#14 Produce native structured object results and second-stage detection verification](https://github.com/Olli0103/unifi-ai-key-emulator/issues/14) | [#2](https://github.com/Olli0103/unifi-ai-key-emulator/issues/2), [#8](https://github.com/Olli0103/unifi-ai-key-emulator/issues/8), [#9](https://github.com/Olli0103/unifi-ai-key-emulator/issues/9), [#7](https://github.com/Olli0103/unifi-ai-key-emulator/issues/7) |
| [#18 Add safe model and search-index migration with rebuild and rollback](https://github.com/Olli0103/unifi-ai-key-emulator/issues/18) | [#8](https://github.com/Olli0103/unifi-ai-key-emulator/issues/8), [#10](https://github.com/Olli0103/unifi-ai-key-emulator/issues/10), [#13](https://github.com/Olli0103/unifi-ai-key-emulator/issues/13) |
| [#21 Implement native basic Find Anything and image search](https://github.com/Olli0103/unifi-ai-key-emulator/issues/21) | [#10](https://github.com/Olli0103/unifi-ai-key-emulator/issues/10), [#14](https://github.com/Olli0103/unifi-ai-key-emulator/issues/14), [#8](https://github.com/Olli0103/unifi-ai-key-emulator/issues/8), [#18](https://github.com/Olli0103/unifi-ai-key-emulator/issues/18) |
| [#22 Implement deep session descriptions and native semantic retrieval](https://github.com/Olli0103/unifi-ai-key-emulator/issues/22) | [#10](https://github.com/Olli0103/unifi-ai-key-emulator/issues/10), [#8](https://github.com/Olli0103/unifi-ai-key-emulator/issues/8), [#12](https://github.com/Olli0103/unifi-ai-key-emulator/issues/12), [#18](https://github.com/Olli0103/unifi-ai-key-emulator/issues/18) |

## 3. Recognition, audio and protection

| Issue | Prerequisites |
| --- | --- |
| [#15 Add native speech transcription with selectable audio providers and models](https://github.com/Olli0103/unifi-ai-key-emulator/issues/15) | [#2](https://github.com/Olli0103/unifi-ai-key-emulator/issues/2), [#8](https://github.com/Olli0103/unifi-ai-key-emulator/issues/8), [#9](https://github.com/Olli0103/unifi-ai-key-emulator/issues/9), [#7](https://github.com/Olli0103/unifi-ai-key-emulator/issues/7), [#5](https://github.com/Olli0103/unifi-ai-key-emulator/issues/5) |
| [#19 Add license-plate recognition and native vehicle metadata](https://github.com/Olli0103/unifi-ai-key-emulator/issues/19) | [#14](https://github.com/Olli0103/unifi-ai-key-emulator/issues/14), [#8](https://github.com/Olli0103/unifi-ai-key-emulator/issues/8), [#7](https://github.com/Olli0103/unifi-ai-key-emulator/issues/7), [#5](https://github.com/Olli0103/unifi-ai-key-emulator/issues/5) |
| [#20 Add face grouping, enrollment and recognition with controlled identity storage](https://github.com/Olli0103/unifi-ai-key-emulator/issues/20) | [#14](https://github.com/Olli0103/unifi-ai-key-emulator/issues/14), [#8](https://github.com/Olli0103/unifi-ai-key-emulator/issues/8), [#3](https://github.com/Olli0103/unifi-ai-key-emulator/issues/3), [#7](https://github.com/Olli0103/unifi-ai-key-emulator/issues/7), [#5](https://github.com/Olli0103/unifi-ai-key-emulator/issues/5) |
| [#23 Add optional native image and face enhancement without altering original evidence](https://github.com/Olli0103/unifi-ai-key-emulator/issues/23) | [#20](https://github.com/Olli0103/unifi-ai-key-emulator/issues/20), [#8](https://github.com/Olli0103/unifi-ai-key-emulator/issues/8) |
| [#26 Integrate AI query matches with native Protect Alarm Manager](https://github.com/Olli0103/unifi-ai-key-emulator/issues/26) | [#14](https://github.com/Olli0103/unifi-ai-key-emulator/issues/14), [#21](https://github.com/Olli0103/unifi-ai-key-emulator/issues/21), [#7](https://github.com/Olli0103/unifi-ai-key-emulator/issues/7) |

## 4. AI Port compatibility and operational qualification

| Issue | Prerequisites |
| --- | --- |
| [#28 Implement the AI Port compatibility profile for Protect and ONVIF cameras](https://github.com/Olli0103/unifi-ai-key-emulator/issues/28) | [#6](https://github.com/Olli0103/unifi-ai-key-emulator/issues/6), [#14](https://github.com/Olli0103/unifi-ai-key-emulator/issues/14), [#9](https://github.com/Olli0103/unifi-ai-key-emulator/issues/9), [#12](https://github.com/Olli0103/unifi-ai-key-emulator/issues/12), [#3](https://github.com/Olli0103/unifi-ai-key-emulator/issues/3), [#15](https://github.com/Olli0103/unifi-ai-key-emulator/issues/15), [#19](https://github.com/Olli0103/unifi-ai-key-emulator/issues/19), [#20](https://github.com/Olli0103/unifi-ai-key-emulator/issues/20), [#21](https://github.com/Olli0103/unifi-ai-key-emulator/issues/21), [#26](https://github.com/Olli0103/unifi-ai-key-emulator/issues/26) |
| [#24 Qualify Mac/NAS deployment, recovery, security and sustained operation](https://github.com/Olli0103/unifi-ai-key-emulator/issues/24) | [#12](https://github.com/Olli0103/unifi-ai-key-emulator/issues/12), [#17](https://github.com/Olli0103/unifi-ai-key-emulator/issues/17), [#3](https://github.com/Olli0103/unifi-ai-key-emulator/issues/3), [#7](https://github.com/Olli0103/unifi-ai-key-emulator/issues/7) |

## Open-source product

| Issue | Prerequisites |
| --- | --- |
| [#4 Establish an open-source license, provenance inventory and independent project identity](https://github.com/Olli0103/unifi-ai-key-emulator/issues/4) | Ready to start |
| [#11 Open contribution, maintenance and private security-reporting workflows](https://github.com/Olli0103/unifi-ai-key-emulator/issues/11) | [#4](https://github.com/Olli0103/unifi-ai-key-emulator/issues/4), [#3](https://github.com/Olli0103/unifi-ai-key-emulator/issues/3) |
| [#25 Build first-run setup, usable documentation and a synthetic demo](https://github.com/Olli0103/unifi-ai-key-emulator/issues/25) | [#17](https://github.com/Olli0103/unifi-ai-key-emulator/issues/17), [#4](https://github.com/Olli0103/unifi-ai-key-emulator/issues/4) |
| [#16 Publish repeatable, signed releases and multi-architecture container images](https://github.com/Olli0103/unifi-ai-key-emulator/issues/16) | [#4](https://github.com/Olli0103/unifi-ai-key-emulator/issues/4), [#3](https://github.com/Olli0103/unifi-ai-key-emulator/issues/3), [#11](https://github.com/Olli0103/unifi-ai-key-emulator/issues/11) |
| [#27 Run a community alpha/beta with public compatibility and quality reports](https://github.com/Olli0103/unifi-ai-key-emulator/issues/27) | [#25](https://github.com/Olli0103/unifi-ai-key-emulator/issues/25), [#16](https://github.com/Olli0103/unifi-ai-key-emulator/issues/16), [#11](https://github.com/Olli0103/unifi-ai-key-emulator/issues/11), [#7](https://github.com/Olli0103/unifi-ai-key-emulator/issues/7), [#24](https://github.com/Olli0103/unifi-ai-key-emulator/issues/24) |
| [#29 Ship and maintain an open-source 1.0 with AI Key and AI Port compatibility contracts](https://github.com/Olli0103/unifi-ai-key-emulator/issues/29) | [#2](https://github.com/Olli0103/unifi-ai-key-emulator/issues/2), [#3](https://github.com/Olli0103/unifi-ai-key-emulator/issues/3), [#6](https://github.com/Olli0103/unifi-ai-key-emulator/issues/6), [#7](https://github.com/Olli0103/unifi-ai-key-emulator/issues/7), [#8](https://github.com/Olli0103/unifi-ai-key-emulator/issues/8), [#9](https://github.com/Olli0103/unifi-ai-key-emulator/issues/9), [#12](https://github.com/Olli0103/unifi-ai-key-emulator/issues/12), [#13](https://github.com/Olli0103/unifi-ai-key-emulator/issues/13), [#17](https://github.com/Olli0103/unifi-ai-key-emulator/issues/17), [#14](https://github.com/Olli0103/unifi-ai-key-emulator/issues/14), [#10](https://github.com/Olli0103/unifi-ai-key-emulator/issues/10), [#21](https://github.com/Olli0103/unifi-ai-key-emulator/issues/21), [#22](https://github.com/Olli0103/unifi-ai-key-emulator/issues/22), [#18](https://github.com/Olli0103/unifi-ai-key-emulator/issues/18), [#15](https://github.com/Olli0103/unifi-ai-key-emulator/issues/15), [#19](https://github.com/Olli0103/unifi-ai-key-emulator/issues/19), [#20](https://github.com/Olli0103/unifi-ai-key-emulator/issues/20), [#23](https://github.com/Olli0103/unifi-ai-key-emulator/issues/23), [#26](https://github.com/Olli0103/unifi-ai-key-emulator/issues/26), [#28](https://github.com/Olli0103/unifi-ai-key-emulator/issues/28), [#24](https://github.com/Olli0103/unifi-ai-key-emulator/issues/24), [#4](https://github.com/Olli0103/unifi-ai-key-emulator/issues/4), [#11](https://github.com/Olli0103/unifi-ai-key-emulator/issues/11), [#25](https://github.com/Olli0103/unifi-ai-key-emulator/issues/25), [#16](https://github.com/Olli0103/unifi-ai-key-emulator/issues/16), [#27](https://github.com/Olli0103/unifi-ai-key-emulator/issues/27), [#5](https://github.com/Olli0103/unifi-ai-key-emulator/issues/5) |
