# Working rules

These are my defaults. Apply each one only where it fits the project type (e.g. UI/auth rules don't apply to a CLI script). Project Notes at the bottom override anything here.

## How I work
- I use the GitHub web UI only. No local git or CLI. Commit and push your changes yourself to `main` as soon as they're ready. If pushing to `main` is blocked, open a PR instead. Never tell me to run git commands.
- Keep answers direct and concise. Ask before big structural changes.
- At the start of a new project, ask me: which host it runs on and what bind storage path to use. Record both in Project Notes.

## Platforms
- Targets, where relevant to the project: Windows, Android, Fedora, Debian, Flatpak, web/PWA, and Chrome (browser extensions included).
- Maximize compatibility within those targets.
- Ignore macOS and iOS unless Project Notes say otherwise.

## Licensing
- Default for personal/Spazcat projects: Apache 2.0. Include `LICENSE` and a `NOTICE` file with the project name and copyright holder.
- If Project Notes ask for stronger protection (e.g. preventing closed hosted forks), use AGPL-3.0.
- Ponsys work projects: proprietary, "Copyright Pontchartrain Systems. All rights reserved." No open-source license unless Project Notes say otherwise.
- Check that every dependency's license is compatible with the project's license, and list third-party licenses where required.

## Docker / compose
- When changing a compose file, give the entire file, with values written inline (no .env files).
- Every service gets a bind-mounted storage path, even if the app stores no data. Use the path recorded in Project Notes.
- Prefer a single container. Avoid multi-service stacks unless clearly needed.
- Never build images on my machines. Dockerfiles live in the repo and images are built by GitHub Actions and published to GHCR.
- Settings should be configurable in the app's UI wherever possible. Environment variables override UI values, and the UI must show when a setting is locked by an env var.
- Work Docker hosts are named after government roles (e.g. Warden, Sergeant, Magistrate). Personal hosts are named after cats (e.g. Cheetah, Housecat, Serval, Singapura).

## GitHub costs (Free plan — never exceed it)
- Private repos share 2,000 Actions minutes/month and 500 MB of storage (Actions artifacts + GitHub Packages combined, across ALL my private repos). Public repos are free. Design every workflow to stay far below these.
- Minutes:
  - Trigger builds on releases/tags or manual dispatch only, never on every push.
  - Use Linux runners. Use Windows runners only for steps that truly require Windows. Never use macOS runners.
  - Cancel superseded runs (`concurrency` with `cancel-in-progress`) and skip jobs when only docs/irrelevant files change.
  - Use Actions caching for dependencies and Docker layers (cache storage is a separate free allowance and saves minutes).
  - Set a `timeout-minutes` on every job so a hung build can't burn the allowance.
- Storage:
  - Release downloads go on GitHub Releases, not artifacts or packages.
  - Avoid Actions artifacts. If one is needed between jobs, set `retention-days: 1`.
  - Docker images: keep them small (slim/alpine or distroless bases, multi-stage builds). For private repos, tell me the image size and ask whether the GHCR package should be public; if it stays private, keep only the latest 1–2 tagged versions.
  - Include a GHCR cleanup step that deletes untagged image versions.
  - Never commit large binaries, build outputs, or generated files. Keep `.gitignore` thorough. No Git LFS.

## Versioning, releases, updates
- Versions are Major.Minor (e.g. 1.1). After .9 roll to the next major (1.9 → 2.0).
- Release executables/packages carry the version in the file name (e.g. `tool-1.3.exe`). The auto-updater must replace the old file and fix anything pointing at it (shortcuts, services, scheduled tasks, .desktop files).
- Include auto-update in any tool where it's viable. Where an install type needs manual steps (e.g. Flatpak), provide a script and instructions.
- In any UI, the displayed version number links to the changelog. When an update is available, show a small dot on the version number, and let the changelog show the newer version's notes.
- Private repos: provide a UI field to store a fine-grained GitHub personal access token (minimum scopes) for update checks and, where included, two-way GitHub issue sync.

## Docs and tracking
- `README.md`: keep it current with features and configuration as they change.
- `CHANGELOG.md`: user-facing notes per version. This is what the UI changelog shows.
- `DEVLOG.md`: working notes for the next session — current state, open issues, decisions, and which GitHub issue each change relates to. Update it as you go so a new session tied to an issue can pick up cold.

## App defaults
- Database: SQLite unless Project Notes say the project needs something bigger.
- Themes: dark and light are always included; default to following the system setting. More themes may be added.
- Responsive layouts must work on standard phones, foldables (folded and unfolded), and tablets.
- Prefer giving the user options/toggles over hard-coding a single behavior.
- Be mindful of API rate limits: cache, back off, and surface limit errors clearly.

## Auth (when a project has auth)
- Support local accounts, OIDC, and passkeys/WebAuthn (fingerprint/face unlock on mobile and in the PWA).
- With OIDC: include an option to redirect to the OIDC provider automatically when the site is opened, with `/login` as a fallback URL that always shows the local login.

## Naming and branding
- When choosing a project name or icon, present many options with reasoning, and consider how each looks at small sizes and in dark/light mode.

## Prompts
- When changing a system prompt, give the whole prompt, not just the changed lines.

## Project Notes
- What it is:
- Stack:
- Host:
- Bind storage path:
- License override (if any):
- DB override (if any):
- Other overrides:
