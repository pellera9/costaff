# Changelog

All notable changes to this project are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/).

This repository is **private** — for internal / paid-tier consumption only.

## [Unreleased]

## [0.1.0-alpha-1] - 2026-05-27

First tagged pre-release of the CoStaff platform core. Snapshots the
Manager Agent, the `costaff` CLI, the platform server + notifier
fanout, the ProgressContext panel pipeline, the IdentityMap channel
routing, the OSS limits / upgrade gating, and the migration to the
A2A-native task model that the sister channel / agent repos build on.

### Notable in this snapshot

- Manager Agent with async ProjectTask + SYSTEM_CALLBACK re-entry for
  long-running sub-agent work.
- Channel notifiers (Telegram / WebChat OSS / WebChat Enterprise) with
  unified `ProgressContext.session_id = task_<id>` panel-key contract
  and IdentityMap-based delivery routing.
- `costaff` CLI: `start` / `stop` / `restart` / `ps`,
  `agent add|list|remove|restart|rebuild`,
  `channel add|list|remove|rebuild`,
  `config show`, `database backup`.
- Dynamic external agent / channel registration via
  `~/.costaff/costaff-agent/<name>` and
  `~/.costaff/costaff-channel/<name>` clone targets, wired into
  docker-compose via per-plugin `compose-fragment.yaml`.
- OSS limits: `max_agents=3`, upgrade pitch on limit errors.

### Added

- `CHANGELOG.md` (this file).

### Version artefacts in this release

- `VERSION` file: `v0.1.0-alpha-1`
- `utils/paths.py` exports `VERSION = "0.1.0-alpha-1"` (read by CLI
  banner + `/api/health`).
- `setup.py` declares `version="0.1.0a1"` (PEP 440 canonical form of
  the same release).
