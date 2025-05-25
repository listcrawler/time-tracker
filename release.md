# Releasing to the release branch

Development takes place in `github.com/listcrawler/time-tracker-dev` (this repo, `origin`).
Releases are published to `github.com/listcrawler/time-tracker` (tracked as the `release` remote).

## One-time setup

Add the release repo as a remote and create a local `release` branch that tracks it:

```bash
git remote add release https://github.com/listcrawler/time-tracker.git
git fetch release
git checkout -b release --track release/main
```

## Releasing

```bash
git checkout release
git merge --squash main
git commit -m "Release $(date +%Y-%m-%d)"
git push release release:main
```

`--squash` stages all changes from `main` as a single commit, keeping the release repo history clean.
