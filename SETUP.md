# vAIsor setup (one time)

This zip has two folders:
- `white-claw/`: agent update (agents work across several repos; planner files goals from ROADMAP.md)
- `vaisor/`: seed files for the new repo

Extract it somewhere outside your repos, e.g. `~/tmp/v4`, then follow the steps.
```bash
mkdir -p ~/tmp/v4 && cd ~/tmp/v4 && unzip -o ~/Downloads/vaisor-setup.zip
```

## 1. Create the repo on GitHub
- New repository: `geeksteev/vaisor`, **Public**
- **Add a license**: MIT (simplest for contributors) or Apache-2.0 (adds a patent grant). Nothing else.

## 2. Push the seed files (you, directly to main, before the ruleset exists)
```bash
git clone https://github.com/geeksteev/vaisor.git ~/projects/vaisor
cp -r ~/tmp/v4/vaisor/. ~/projects/vaisor/
cd ~/projects/vaisor && git add -A && git commit -m "Seed: CI, project context, roadmap" && git push
```

## 3. Repo settings (same as animatronic-head)
- Settings → General → Pull Requests: **Allow auto-merge** on; allow **squash merging** only.
- Settings → Rules → Rulesets → New branch ruleset `protect-main`: Active, default branch, restrict deletions,
  block force pushes, require a PR (0 approvals), require status checks `ci` and `ai-review`,
  require branches up to date. After the first CI run, pin `ci` to the GitHub Actions source.
- Labels: create `goal` and `agent-ready` (the scripts create the rest).

## 4. Give each agent token access to vaisor
GitHub → Settings → Developer settings → Fine-grained tokens. For **each** of `agent_token`,
`agent_reviewer` and `agent_planner`: Edit → Repository access → add `geeksteev/vaisor` → Update.
Permissions stay the same.

## 5. Update the agents
```bash
cp -r ~/tmp/v4/white-claw/. ~/projects/white-claw/
cd ~/projects/white-claw && git status     # ~10 files under ansible/roles/agent-workstation/
cd ansible && ansible-playbook playbooks/agents.yml
cd .. && git add -A && git commit -m "Agents work across repos; planner files goals from ROADMAP.md" && git push
```
The playbook moves each agent's existing animatronic-head state into the new per-repo layout
automatically; nothing in flight is lost.

## 6. That's it
Within ~10 minutes the planner sees `ROADMAP.md` in vaisor, files **Goal: Core, bench.yaml and the
first driver**, plans it, and the builder starts. When a goal's issues are all closed the planner
closes it and files the next roadmap goal. Edit `ROADMAP.md` (via an issue, or directly) to change direction.

Watch all three:
```bash
for h in 10 11 12; do ssh -J mgmt-jump agent@10.10.105.$h 'journalctl -u white-claw-agent -n 5 --no-pager'; done
```
