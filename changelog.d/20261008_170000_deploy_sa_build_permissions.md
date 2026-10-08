### Fixed

- The deploy guide's service account setup was missing the roles Cloud Build
  needs to accept a source upload, so the first CI deploy failed with
  "forbidden from accessing the bucket". It now grants
  `serviceusage.serviceUsageConsumer` and `storage.bucketViewer` on the
  project, and `storage.objectAdmin` on the Cloud Build bucket.
- The deploy workflow failed after a successful image build, because
  streaming Cloud Build's logs needs project-wide Viewer. It now submits the
  build asynchronously and polls its status; the logs stay in the console.
