# Generic configuration patches and credential destinations (#3 / #13)

29 October 2026. Synthetic configuration files only; no real secret, media or
network request was used. This is a prospective credential-forwarding code path,
not an observed production disclosure.

The dedicated vision-provider operation replaces the role's selection and drops
its old key when the provider/endpoint changes. A generic JSON merge patch did
not use that operation. It could change an endpoint while retaining the previous
`api_key_file` or `bearer_token_file`, which the next runtime would read for the
new service. The generic write-only field rule prevented editing those secret
references but did not prevent silently retaining them at a changed destination.

The same class affected the database password reference when changing database
host, port or TLS trust. Retaining that password at a different service/trust
boundary must be a separate, reviewed operation, not an incidental merge patch.

Generic previews and applies now refuse a destination/adapter change that retains
an existing credential in vision, text embeddings, speech or the database. The
check occurs before archiving or replacing configuration. Rejection preserves
configuration, secret bytes and history; an apply may use its ordinary private
lock file. No secret value/path is included in the fixed error.

Controls retain supported behavior:

- a model-only change on the same endpoint keeps its own role key;
- the dedicated provider operation still drops the old key on a new endpoint;
- its recorded rollback restores the exact prior configuration and original key
  binding, instead of forwarding a current key to an old service;
- an uncredentialed destination change has no old secret to retarget.

Twelve preview/apply rejection cases fail on the old main baseline: vision,
embeddings, speech and database host/port/trust changes. Positive preservation
and role-scoped rollback cases continue to pass. This does not implement missing
role-specific UI operations or resolve arbitrary-hostname DNS rebinding; those
requirements remain open in #3, #13 and #17. No deployed service was changed.
