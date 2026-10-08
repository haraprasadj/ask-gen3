### Fixed

- A full index build copied the README and docs of every uc-cdis repository,
  including six with no licence, into an artifact that is published and baked
  into the served image. Repositories without an open licence on GitHub are now
  skipped, and each indexed repository's SPDX licence is recorded.
