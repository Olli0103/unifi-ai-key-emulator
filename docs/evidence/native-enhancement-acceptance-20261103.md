# Native on-demand enhancement acceptance — 3 November 2026

Acceptance for [#23](https://github.com/Olli0103/unifi-protect-local-ai/issues/23),
using the existing optional enhancement adapter. This change adds regression
coverage and evidence, not a new production adapter or activation. The separate
unfinished control-site enhancement form is not included.

## Original acceptance

| Requirement | Evidence |
| --- | --- |
| Protect displays an enhanced derivative through its native action, retaining original footage | One native **Enhance** action, persisted derivative, exact stored/displayed byte match, native original/derivative toggle, unchanged original displayed bytes and subsequent original recording playback |
| Failure, unsupported input and model changes are visible and safe | Existing refusal/output/failure fixtures, plus an actual loopback replacement-service lifecycle exercising completed-job retention, new-object success, unsupported output and unavailable replacement; journal outcomes and health counters checked |

The supported model slot is the independent optional
`face_enhancement.server`, separate from caption/recognition providers. Its local
service selects the enhancement model. Settings are loaded at worker startup;
changing that choice is not a hot model swap or an automatic image sweep.

## Native observation

Protect **7.3.74**, 3 November, 14:35–14:47 UTC. The local service already reported
GFPGAN 1.4 loaded on the NPU, the Key already advertised enhancement, and native
enhancement settings were already enabled. None of those settings was changed.
An existing detection with the owner's existing native group label was selected;
no identity was inferred from an image, enrolled, named or reassigned.

1. The original displayed crop decoded at **230 × 230**, **11,499 bytes**. Its
   private SHA-256 was retained before the action. Original recording playback
   was inspected in Protect.
2. In **Faces → Most Recent**, the selected detection's native hover menu offered
   **Enhance**. One native click produced an HTTP 200 action response and the
   visible **Edge AI processing** state. No direct API mutation or old-event
   replay was used. Commonality/group tiles do not expose the same action.
3. The exact worker job, derived with the installed `enhanceImage:<object>`
   constructor, completed. It reported an enhanced **47,726-byte** derivative and
   an accepted callback. Service counters advanced to **one request, one enhanced,
   one aligned, zero declined and zero failed**.
4. A fresh bounded native **detection-search** read matched the original event,
   camera and object and returned a separate `enhancedImage` with
   `taskState: done`. The original `croppedId` was unchanged. The older plain
   event-detail response did **not** contain this joined enhancement field; that
   initial missing field was not treated as proof that saving had failed.
5. GET of the named native enhanced-thumbnail route returned the derivative.
   The decoded displayed image, **573 × 573**, had exactly the same bytes and
   SHA-256 as that stored derivative, and differed from the original. Actual
   screenshots were inspected, including Protect's enhancement icon.
6. Native **Remove Enhancement** set the separate derivative's `settings.hide`
   to true, retaining it with `taskState: done`. The displayed original returned
   at **230 × 230**, **11,499 bytes**, with its exact pre-action SHA-256.
   Native **Enhance** then set hide false and displayed the same saved derivative;
   the inference counter remained **one**, not two. This is a display toggle,
   not deletion or regeneration.
7. Original recording playback was inspected again after these actions. Video
   elements were playing, decoded and error-free. The final native read retained
   one completed enhancement, five adopted processors, nine Port camera pairings
   and all eleven cameras recording. Ten relevant process identities and the
   actual saved Key configuration hash matched the pre-action observation.

Only the derivative and its native display setting were written. No service was
restarted, no recognition/template/zone/pairing/recording setting changed, and no
alarm was enabled. Private crops, screenshots, object IDs, group names, addresses,
model requests and recording content are not published here.

## Source and synthetic qualification

- **48 tests pass** across `test_face_enhancement.py`,
  `test_enhancement_role_change.py` and `test_vision_server.py`.
- Existing cases cover separate derivative upload, 204/no modification, smaller,
  PNG and oversized outputs, unavailable service, malformed task/object/crop
  binding, disabled role, nonlocal destination, redirects, huge declared JPEG,
  EXIF/ICC removal and separation from the controller identity-bearing session.
- Three replacement-service cases use real bounded loopback HTTP and fictional
  images. A completed object is not recomputed or uploaded again after loading
  another enhancement role, and its journal remains byte-identical. A **new**
  object reaches only the replacement service: a valid JPEG produces a new
  derivative, PNG produces native **no modification**, and HTTP 503 produces a
  visible failed journal without an upload. Original bytes, the earlier upload
  and caption-provider configuration remain unchanged. Counter/journal outcomes
  distinguish uploaded, declined/rejected and failed; failure is not success.
- Installed `_normalize_enhance` and `_execute_enhance` source matches the reviewed
  source exactly. The enhanced multipart branch is AST-identical, including exact
  `text/plain` fields. The **whole** callback method is not byte-identical: the
  installed revision has an observer decorator and typed callback HTTP error.
  Those differences are retained, not hidden by a whole-runtime equality claim.
- The adapter's existing task deadline is at most 60 seconds; existing media,
  decoded-dimension, response and destination limits remain in force.

Changing a configured service is the supported model-change boundary tested
here. This does not attest to weights silently replaced behind an unchanged
address, verify an arbitrary service's privacy or measure restoration accuracy.
The generated derivative is distinctly optional display material, **not factual
reconstruction or recognition evidence**. Existing derivatives are retained,
not automatically relabelled or regenerated after a model/service change.

No recognition-quality, identity/grouping accuracy, automatic enhancement sweep,
all-model compatibility, release qualification or downstream #17/#20 completion
is claimed. Negative/model-change results above are synthetic, not injected
production failures. Full exact-head CI is recorded separately by the PR checks.
