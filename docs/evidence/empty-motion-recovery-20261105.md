# Empty first-frame motion recovery — 5 November 2026

## Native failure retained

A confirmed owner passage on the original test camera produced a saved motion
event, but no person event or execution of the owner-only AI-query test alarm.
The original recording visibly contains a person. The exact camera binding and
per-minute detector diagnostics show one motion-gated request, one clean empty
answer and no accepted object in that minute. There was no request failure,
budget deferral or backoff skip in the observed minute. The earlier confirmed
passage had two requests/two accepted objects and a saved native caption.

The test rule was restored to paused after approximately 249 seconds, before
its announced deadline; a separate read matched all 33 alarm configurations to
their baseline. No other rule, camera, zone, threshold or model was changed.
The failed positive is not a successful nonmatch test, an alarm-delivery result
or a Deep-session result. Private recordings and identifiers are not published.

The actual model input frame and exact request timing were not retained.
Therefore an early empty frame versus a model miss cannot be distinguished from
these aggregate observations. The source limitation below is reproduced, not
claimed as the conclusively established cause of that particular empty reply.

## Bounded source correction

Previously a clean empty first motion response discarded the confirming sample
and left the gate disarmed until three quiet frames. Continued movement could
not cause another request in that episode, even if the object entered later.

Only a clean empty **first, fresh motion** response now retains a ten-second
allowance. A subsequent frame must cross the existing motion-change threshold
to use it; an unchanged image is not retried. The allowance permits at most two
new samples, both of which must meet the unchanged detection/tracking criteria
to create a confirmed track. Another empty, doubtful, rejected or invalid reply
ends recovery. Recovery cannot invoke the separate near-threshold extension.

Startup, existing-presence refreshes, rejected-object responses and failures do
not create this allowance. Three quiet frames, expiry, reset, cancellation or
deferral clear it. Cameras remain isolated. Existing provider backoff, durable
request cap and fresh-motion reserve still apply to every request. The optional
content-free `empty_motion_recoveries` health count measures gate admissions,
not provider calls, successful detections or delivered alarms.

## Verification and limits

Against unchanged parent `d1ac600`, the initial new regression file produced
seven failures and four passing refusal controls. After the correction and two
additional camera/quiet-scene controls, the focused detection, near-threshold,
new recovery, budget and tracker group passes 141 tests. Static and whitespace
checks pass. The existing continuous-motion test now expects one bounded
recovery and separately verifies the unchanged fresh-motion budget reserve.

All fixtures and model replies are fictional; no model requests or production
deployment are performed by these tests. Native post-deployment recovery and
positive/negative query-alarm acceptance remain unproved. Issues #26 and #74
remain open; no issue-closing reference is intended.
