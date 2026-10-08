### Fixed

- The deploy guide's service account setup was missing the roles Cloud Build
  needs to accept a source upload, so the first CI deploy failed with
  "forbidden from accessing the bucket". It now grants
  `serviceusage.serviceUsageConsumer` and `storage.bucketViewer` on the
  project, and `storage.objectAdmin` on the Cloud Build bucket.
