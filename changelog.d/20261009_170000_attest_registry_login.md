### Fixed

- The first deploy from the public repository failed at the provenance
  attestation with "No credentials found for registry". The deploy now logs
  in to Artifact Registry with a stored, short-lived token, which the
  attestation's push can use, instead of a gcloud credential helper, which
  it cannot.
